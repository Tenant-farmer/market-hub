"""종목 상세 정보 — yfinance 온디맨드 조회 + 6시간 캐시 (stock_detail 테이블).

500종목을 미리 긁지 않고 클릭 시 조회한다. 섹션별 실패는 격리 (부분 렌더).
"""
import json
import math
from datetime import datetime

import pandas as pd
import yfinance as yf

CACHE_TTL_H = 6

RECO_KO = {
    "strong_buy": "적극 매수", "buy": "매수", "hold": "중립",
    "underperform": "비중 축소", "sell": "매도", "none": "–",
}


def _pct(x):
    return round(x * 100, 2) if isinstance(x, (int, float)) and not math.isnan(x) else None


def _num(x):
    return x if isinstance(x, (int, float)) and not (isinstance(x, float) and math.isnan(x)) else None


def get_detail(con, sym: str, force: bool = False) -> dict | None:
    con.execute(
        "CREATE TABLE IF NOT EXISTS stock_detail "
        "(symbol TEXT PRIMARY KEY, json TEXT, fetched_at TEXT)"
    )
    if not force:
        row = con.execute(
            "SELECT json, fetched_at FROM stock_detail WHERE symbol=?", (sym,)
        ).fetchone()
        if row:
            age_h = (datetime.now() - datetime.fromisoformat(row["fetched_at"])).total_seconds() / 3600
            if age_h < CACHE_TTL_H:
                d = json.loads(row["json"])
                d["fetched_at"] = row["fetched_at"]
                d["cached"] = True
                return d
    d = _fetch(sym)
    if d:
        now = datetime.now().isoformat(timespec="seconds")
        con.execute(
            "INSERT OR REPLACE INTO stock_detail (symbol, json, fetched_at) VALUES (?,?,?)",
            (sym, json.dumps(d, ensure_ascii=False), now),
        )
        con.commit()
        d["fetched_at"] = now
        d["cached"] = False
    return d


def _monthly_map(closes: pd.Series) -> dict | None:
    """월봉 종가 시리즈 → 연×월 수익률 히트맵 데이터."""
    rets = closes.pct_change() * 100
    by: dict[int, dict[int, float]] = {}
    for ts, v in rets.items():
        if pd.isna(v):
            continue
        by.setdefault(ts.year, {})[ts.month] = round(float(v), 1)
    if not by:
        return None
    years = sorted(by, reverse=True)
    avg = []
    for m in range(1, 13):
        vals = [by[y][m] for y in years if m in by[y]]
        avg.append(round(sum(vals) / len(vals), 1) if vals else None)
    return {
        "years": [{"y": y, "m": [by[y].get(m) for m in range(1, 13)]} for y in years],
        "avg": avg,
        "cur": [closes.index[-1].year, closes.index[-1].month],
    }


def get_detail_kr(con, code: str, sector_code: str, force: bool = False) -> dict | None:
    """KR 종목 상세 — 로컬 DB + pykrx(수급·펀더멘털·공매도·월봉) + 야후(애널리스트, 보조)."""
    con.execute(
        "CREATE TABLE IF NOT EXISTS stock_detail "
        "(symbol TEXT PRIMARY KEY, json TEXT, fetched_at TEXT)"
    )
    if not force:
        row = con.execute(
            "SELECT json, fetched_at FROM stock_detail WHERE symbol=?", (code,)
        ).fetchone()
        if row:
            age_h = (datetime.now() - datetime.fromisoformat(row["fetched_at"])).total_seconds() / 3600
            if age_h < CACHE_TTL_H:
                d = json.loads(row["json"])
                d["fetched_at"] = row["fetched_at"]
                d["cached"] = True
                return d
    d = _fetch_kr(con, code, sector_code)
    if d:
        now = datetime.now().isoformat(timespec="seconds")
        con.execute(
            "INSERT OR REPLACE INTO stock_detail (symbol, json, fetched_at) VALUES (?,?,?)",
            (code, json.dumps(d, ensure_ascii=False), now),
        )
        con.commit()
        d["fetched_at"] = now
        d["cached"] = False
    return d


def _fetch_kr(con, code: str, sector_code: str) -> dict | None:
    from datetime import date, timedelta

    # 로컬: 가격·52주·시총
    rows = con.execute(
        "SELECT date, close FROM prices_daily WHERE symbol=? ORDER BY date DESC LIMIT 252",
        (code,),
    ).fetchall()
    if len(rows) < 2:
        return None
    closes = [r["close"] for r in rows]
    price, prev = closes[0], closes[1]
    lo, hi = min(closes), max(closes)
    meta = con.execute("SELECT mcap FROM stock_meta WHERE symbol=?", (code,)).fetchone()
    d = {
        "symbol": code, "kq": sector_code.startswith("2"),
        "price": price, "prev": prev,
        "chg": round((price / prev - 1) * 100, 2),
        "w52_lo": lo, "w52_hi": hi,
        "w52_pos": round((price - lo) / (hi - lo) * 100, 1) if hi > lo else None,
        "mcap": meta["mcap"] if meta else None,
    }

    today = date.today()
    e_ymd = today.strftime("%Y%m%d")
    try:
        from pykrx import stock as krx

        # 펀더멘털 (KRX 공식: PER/PBR/EPS/BPS/DIV/DPS)
        try:
            f = krx.get_market_fundamental(
                (today - timedelta(days=10)).strftime("%Y%m%d"), e_ymd, code
            )
            if len(f):
                last = f.iloc[-1]
                d.update({
                    "per": _num(float(last["PER"])) or None,
                    "pbr": _num(float(last["PBR"])) or None,
                    "eps": _num(float(last["EPS"])), "bps": _num(float(last["BPS"])),
                    "div": _num(float(last["DIV"])), "dps": _num(float(last["DPS"])),
                })
        except Exception:
            pass

        # 종목별 수급 90일 (외인/기관/개인 누적, 억원)
        try:
            tv = krx.get_market_trading_value_by_date(
                (today - timedelta(days=130)).strftime("%Y%m%d"), e_ymd, code
            )
            inv_map = {"외국인합계": "foreign", "기관합계": "institution", "개인": "individual"}
            series = {v: [] for v in inv_map.values()}
            cum = {v: 0.0 for v in inv_map.values()}
            sums = {v: {"d5": 0.0, "d20": 0.0, "d60": 0.0} for v in inv_map.values()}
            n = len(tv)
            for i, (ts, r) in enumerate(tv.iterrows()):
                for kr_k, k in inv_map.items():
                    val = float(r.get(kr_k, 0) or 0)
                    cum[k] += val
                    series[k].append({"time": str(ts.date()), "value": round(cum[k] / 1e8, 1)})
                    left = n - i
                    if left <= 5:
                        sums[k]["d5"] += val
                    if left <= 20:
                        sums[k]["d20"] += val
                    if left <= 60:
                        sums[k]["d60"] += val
            d["flows"] = {"series": series, "sums": sums, "n": n}
        except Exception:
            d["flows"] = None

        # 공매도 잔고 (최근)
        try:
            sb = krx.get_shorting_balance_by_date(
                (today - timedelta(days=30)).strftime("%Y%m%d"), e_ymd, code
            )
            if len(sb):
                d["short"] = {
                    "pct": round(float(sb["비중"].iloc[-1]), 2),
                    "date": str(sb.index[-1].date()),
                }
        except Exception:
            pass

        # 5년 월봉 → 월별 수익률
        try:
            m = krx.get_market_ohlcv(
                (today - timedelta(days=365 * 5 + 30)).strftime("%Y%m%d"), e_ymd, code, freq="m"
            )
            d["monthly"] = _monthly_map(m["종가"])
        except Exception:
            d["monthly"] = None
    except Exception:
        d["flows"] = d.get("flows")

    # 야후 보조 (대형주 애널리스트 컨센서스·기업 개요·CapEx 추이)
    suffix = ".KQ" if d["kq"] else ".KS"
    yft = yf.Ticker(f"{code}{suffix}")
    try:
        info = yft.info or {}
        g = info.get
        tgt = _num(g("targetMeanPrice"))
        d.update({
            "tgt_mean": tgt,
            "n_analysts": _num(g("numberOfAnalystOpinions")),
            "reco": RECO_KO.get(g("recommendationKey") or "none", "–"),
            "reco_mean": _num(g("recommendationMean")),
            "upside": round((tgt / price - 1) * 100, 1) if tgt and price else None,
            "summary": (g("longBusinessSummary") or "")[:900],
            "website": g("website") or "",
        })
    except Exception:
        pass

    # CapEx 추이 — 분기 5개 + 연간 4개 (현금흐름표)
    try:
        def _capex_series(cf):
            idx = [i for i in cf.index if "Capital Expenditure" in str(i)]
            if not idx:
                return []
            s = cf.loc[idx[0]].dropna().sort_index()
            return [{"label": str(ts.date()), "v": abs(float(v))} for ts, v in s.items()]

        q = _capex_series(yft.quarterly_cashflow)[-5:]
        y = _capex_series(yft.cashflow)[-4:]
        yoy = (
            round((q[-1]["v"] / q[-5]["v"] - 1) * 100, 1)
            if len(q) >= 5 and q[-5]["v"] else None
        )
        d["capex"] = {"q": q, "y": y, "yoy": yoy} if (q or y) else None
    except Exception:
        d["capex"] = None
    return d
    t = yf.Ticker(sym)
    try:
        info = t.info or {}
    except Exception:
        return None
    g = info.get
    price = _num(g("currentPrice")) or _num(g("previousClose"))
    if not price:
        return None
    prev = _num(g("previousClose"))
    lo, hi = _num(g("fiftyTwoWeekLow")), _num(g("fiftyTwoWeekHigh"))
    tgt_med = _num(g("targetMedianPrice"))
    d = {
        "symbol": sym,
        "name": g("longName") or sym,
        "sector": g("sector") or "", "industry": g("industry") or "",
        "website": g("website") or "",
        "summary": (g("longBusinessSummary") or "")[:900],
        "price": price, "prev": prev,
        "chg": round((price / prev - 1) * 100, 2) if prev else None,
        "w52_lo": lo, "w52_hi": hi,
        "w52_pos": round((price - lo) / (hi - lo) * 100, 1) if lo and hi and hi > lo else None,
        "mcap": _num(g("marketCap")), "volume": _num(g("volume")),
        "per": _num(g("trailingPE")), "fper": _num(g("forwardPE")),
        "pbr": _num(g("priceToBook")),
        "roe": _pct(g("returnOnEquity")), "roa": _pct(g("returnOnAssets")),
        "npm": _pct(g("profitMargins")), "opm": _pct(g("operatingMargins")),
        "rev_g": _pct(g("revenueGrowth")), "eps_g": _pct(g("earningsGrowth")),
        "dte": _num(g("debtToEquity")),
        "div_yield": _num(g("dividendYield")),  # 야후가 이미 % 단위로 제공
        "payout": _pct(g("payoutRatio")),
        "div_last": _num(g("lastDividendValue")),
        "eps": _num(g("trailingEps")), "bps": _num(g("bookValue")),
        "ocf": _num(g("operatingCashflow")),
        "debt": _num(g("totalDebt")), "cash": _num(g("totalCash")),
        "tgt_mean": _num(g("targetMeanPrice")), "tgt_med": tgt_med,
        "tgt_hi": _num(g("targetHighPrice")), "tgt_lo": _num(g("targetLowPrice")),
        "n_analysts": _num(g("numberOfAnalystOpinions")),
        "reco": RECO_KO.get(g("recommendationKey") or "none", g("recommendationKey") or "–"),
        "reco_mean": _num(g("recommendationMean")),
        "upside": round((tgt_med / price - 1) * 100, 1) if tgt_med else None,
        "ins_pct": _pct(g("heldPercentInsiders")), "inst_pct": _pct(g("heldPercentInstitutions")),
    }
    try:
        ex = g("exDividendDate")
        d["ex_div"] = datetime.fromtimestamp(ex).date().isoformat() if ex else None
    except Exception:
        d["ex_div"] = None

    # 실적: 다음 발표 + 최근 4분기 서프라이즈
    d["next_earn"], d["eps_hist"] = None, []
    try:
        ed = t.earnings_dates
        if ed is not None and len(ed):
            for idx, r in ed.iterrows():
                est = _num(r.get("EPS Estimate"))
                act = _num(r.get("Reported EPS"))
                row = {"date": str(idx.date()), "est": est, "act": act,
                       "surp": _num(r.get("Surprise(%)"))}
                if act is None:
                    d["next_earn"] = row  # 미래 행이 위쪽 — 마지막(가장 가까운) 미래가 남음
                elif len(d["eps_hist"]) < 4:
                    d["eps_hist"].append(row)
    except Exception:
        pass

    # 기관투자자
    d["inst"] = []
    try:
        ih = t.institutional_holders
        for _, r in ih.head(8).iterrows():
            d["inst"].append({
                "holder": str(r.get("Holder", "")),
                "pct": _pct(r.get("pctHeld")),
                "date": str(r.get("Date Reported", ""))[:10],
            })
    except Exception:
        pass

    # 내부자 거래
    d["insiders"] = []
    try:
        ins = t.insider_transactions
        for _, r in ins.head(6).iterrows():
            d["insiders"].append({
                "date": str(r.get("Start Date", ""))[:10],
                "insider": str(r.get("Insider", "")),
                "position": str(r.get("Position", ""))[:30],
                "shares": _num(r.get("Shares")),
                "value": _num(r.get("Value")),
                "text": str(r.get("Text", ""))[:60],
            })
    except Exception:
        pass

    # 뉴스
    d["news"] = []
    try:
        for n in (t.news or [])[:8]:
            c = n.get("content") or n
            if not isinstance(c, dict) or not c.get("title"):
                continue
            prov = c.get("provider") or {}
            url = (c.get("canonicalUrl") or {}).get("url") or (c.get("clickThroughUrl") or {}).get("url") or ""
            d["news"].append({
                "title": c["title"], "src": prov.get("displayName", ""),
                "date": (c.get("pubDate") or "")[:10], "url": url,
            })
    except Exception:
        pass

    # 월별 수익률 (5년)
    d["monthly"] = None
    try:
        h = t.history(period="5y", interval="1mo")["Close"].dropna()
        rets = h.pct_change() * 100
        by: dict[int, dict[int, float]] = {}
        for ts, v in rets.items():
            if pd.isna(v):
                continue
            by.setdefault(ts.year, {})[ts.month] = round(float(v), 1)
        if by:
            years = sorted(by, reverse=True)
            avg = []
            for m in range(1, 13):
                vals = [by[y][m] for y in years if m in by[y]]
                avg.append(round(sum(vals) / len(vals), 1) if vals else None)
            d["monthly"] = {
                "years": [{"y": y, "m": [by[y].get(m) for m in range(1, 13)]} for y in years],
                "avg": avg,
                "cur": [h.index[-1].year, h.index[-1].month],
            }
    except Exception:
        pass
    return d
