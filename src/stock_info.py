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


def _fetch(sym: str) -> dict | None:
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
