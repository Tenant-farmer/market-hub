"""가상장부 — 모멘텀·단타 전략을 브로커에 안 보내고 DB에서 종가 기준으로 시뮬.

로테이션(실제 모의계좌)과 평단이 겹치지 않게 완전 분리된 A/B 실험용 장부.
검증 근거(scripts/): universe_compare(3개월 모멘텀 최강)·timing_by_vix(공포장 매수 우위)·
reality_check(월1회 재평가 스윗스팟, 생존편향 감안).

전략 2종 (각 가상 시드 $100k, 최대 SLOTS 슬롯 균등):
- momentum(공격): rs63(3개월 시장대비) 상위 진입, 종가>50MA. 청산: rs63 하위50% 이탈 /
  손절 -8% / 21일 재평가. 승자를 오래, 패자는 자름
- meanrev(단타): RSI2<10 극단과매도 & 종가>200MA(추세유지) 진입. 청산: 종가>5MA 반등 /
  5일 경과 / 손절 -5%. 짧은 보유
- VIX≥25 공포장이면 SLOTS를 1.5배로(검증: 공포장 매수 승률 70%+ — 실탄 집중)

데이터: 라이브는 yfinance 최신 유니버스(refresh_prices), 검증/dry는 캐시 재사용.
process(con, px, spy, vix, asof)가 하루치 청산·진입을 장부에 반영. 실제 주문 없음.
"""
import pickle
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "data" / "us_px_cache.pkl"
SEED = 100_000.0
SLOTS = 10
STRATS = ("momentum", "meanrev")


def ensure(con):
    con.execute("""CREATE TABLE IF NOT EXISTS daytrade_ledger (
        id INTEGER PRIMARY KEY AUTOINCREMENT, strategy TEXT, symbol TEXT,
        entry_date TEXT, entry_px REAL, qty REAL, status TEXT,
        exit_date TEXT, exit_px REAL, exit_reason TEXT, pnl_pct REAL)""")
    con.execute("""CREATE TABLE IF NOT EXISTS daytrade_equity (
        date TEXT, strategy TEXT, equity REAL, cash REAL, n_open INTEGER,
        PRIMARY KEY (date, strategy))""")


def _rsi(s, n):
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def _indicators(px, spy):
    """유니버스 지표: rs63, rsi2, 50MA/200MA/5MA 대비 위치 (최신 열만 필요)."""
    spy63 = spy / spy.shift(63) - 1
    out = {}
    for sym in px.columns:
        c = px[sym].dropna()
        if len(c) < 210:
            continue
        out[sym] = {
            "close": c.iloc[-1],
            "rs63": (c.iloc[-1] / c.iloc[-64] - 1) - float(spy63.reindex(c.index).iloc[-1]),
            "rsi2": _rsi(c, 2).iloc[-1],
            "ma50": c.iloc[-50:].mean(), "ma200": c.iloc[-200:].mean(),
            "ma5": c.iloc[-5:].mean(),
        }
    df = pd.DataFrame(out).T
    df["rs63_pct"] = df["rs63"].rank(pct=True)      # 유니버스 내 백분위
    return df


def _equity(con, strat, ind):
    """전략의 현재 평가자산 = 현금 + 보유 평가액 (실현손익 누적 기반)."""
    row = con.execute("SELECT COALESCE(SUM(pnl_pct*entry_px*qty),0) rp, "
                      "COALESCE(SUM(entry_px*qty),0) inv "
                      "FROM daytrade_ledger WHERE strategy=? AND status='closed'",
                      (strat,)).fetchone()
    realized = row["rp"] / 100.0 if row["rp"] else 0.0
    open_pos = con.execute("SELECT symbol, entry_px, qty FROM daytrade_ledger "
                           "WHERE strategy=? AND status='open'", (strat,)).fetchall()
    cost_open = sum(p["entry_px"] * p["qty"] for p in open_pos)
    mkt_open = sum((float(ind.loc[p["symbol"], "close"]) if p["symbol"] in ind.index
                    else p["entry_px"]) * p["qty"] for p in open_pos)
    cash = SEED + realized - cost_open
    return cash + mkt_open, cash, len(open_pos)


def process(con, px, spy, vix_level, asof) -> dict:
    """하루치 청산→진입 반영. 반환: {strategy: {closed, opened, equity}}."""
    ensure(con)
    ind = _indicators(px, spy)
    slots = int(SLOTS * 1.5) if (vix_level or 0) >= 25 else SLOTS   # 공포장 실탄 집중
    result = {}
    for strat in STRATS:
        closed, opened = [], []
        open_pos = con.execute("SELECT id, symbol, entry_px, qty FROM daytrade_ledger "
                               "WHERE strategy=? AND status='open'", (strat,)).fetchall()
        held = set()
        # ---- 청산 ----
        for p in open_pos:
            if p["symbol"] not in ind.index:
                held.add(p["symbol"]); continue
            r = ind.loc[p["symbol"]]
            px_now, ret = float(r["close"]), float(r["close"]) / p["entry_px"] - 1
            reason = None
            if strat == "momentum":
                if ret <= -0.08:
                    reason = "손절"
                elif r["rs63_pct"] < 0.50:
                    reason = "주도이탈"
            else:
                held_days = _bdays(p["symbol"], strat, con, asof)
                if ret <= -0.05:
                    reason = "손절"
                elif float(r["close"]) > float(r["ma5"]):
                    reason = "반등청산"
                elif held_days >= 5:
                    reason = "기간청산"
            if reason:
                con.execute("UPDATE daytrade_ledger SET status='closed', exit_date=?, "
                            "exit_px=?, exit_reason=?, pnl_pct=? WHERE id=?",
                            (asof, px_now, reason, round(ret * 100, 2), p["id"]))
                closed.append((p["symbol"], reason, round(ret * 100, 2)))
            else:
                held.add(p["symbol"])
        # ---- 진입 (빈 슬롯 만큼) ----
        free = slots - len(held)
        if free > 0:
            cand = _candidates(strat, ind, held)[:free]
            _, cash, _ = _equity(con, strat, ind)
            per = max(cash, 0) / slots
            for sym in cand:
                pxs = float(ind.loc[sym, "close"])
                qty = round(per / pxs, 4) if pxs > 0 else 0
                if qty <= 0:
                    continue
                con.execute("INSERT INTO daytrade_ledger (strategy, symbol, entry_date, "
                            "entry_px, qty, status) VALUES (?,?,?,?,?, 'open')",
                            (strat, sym, asof, pxs, qty))
                opened.append(sym)
        eq, cash, nopen = _equity(con, strat, ind)
        con.execute("INSERT OR REPLACE INTO daytrade_equity VALUES (?,?,?,?,?)",
                    (asof, strat, round(eq, 2), round(cash, 2), nopen))
        result[strat] = {"closed": closed, "opened": opened, "equity": round(eq, 2)}
    con.commit()
    return result


def _candidates(strat, ind, held):
    df = ind[~ind.index.isin(held)]
    if strat == "momentum":
        df = df[(df["rs63_pct"] >= 0.90) & (df["close"] > df["ma50"])]
        return df.sort_values("rs63", ascending=False).index.tolist()
    df = df[(df["rsi2"] < 10) & (df["close"] > df["ma200"])]
    return df.sort_values("rsi2").index.tolist()          # 더 과매도 우선


def _bdays(sym, strat, con, asof):
    r = con.execute("SELECT entry_date FROM daytrade_ledger WHERE strategy=? AND symbol=? "
                    "AND status='open' ORDER BY id DESC LIMIT 1", (strat, sym)).fetchone()
    if not r:
        return 0
    return np.busday_count(r["entry_date"], asof)


def refresh_prices():
    """라이브용 유니버스 최신 종가 — 캐시가 있으면 최근분만 증분 갱신은 생략(일 1회 전체)."""
    import yfinance as yf

    from src import db
    con = db.connect()
    syms = sorted(r["stock_code"] for r in con.execute(
        "SELECT DISTINCT stock_code FROM sector_map WHERE market='US_STOCK'"))
    con.close()
    frames = []
    for i in range(0, len(syms), 100):
        d = yf.download(syms[i:i + 100], period="400d", auto_adjust=True, progress=False,
                        group_by="column", threads=True)["Close"]
        frames.append(d if isinstance(d, pd.DataFrame) else d.to_frame(syms[i]))
    px = pd.concat(frames, axis=1).ffill()
    spy = yf.download("SPY", period="400d", auto_adjust=True, progress=False)["Close"]
    spy = spy["SPY"] if isinstance(spy, pd.DataFrame) else spy
    px = px.loc[:, px.notna().sum() >= 210]
    CACHE.write_bytes(pickle.dumps((px, spy)))
    return px, spy


if __name__ == "__main__":
    import sys

    from dotenv import load_dotenv
    load_dotenv()
    sys.path.insert(0, ".")
    from src import db

    dry = "--dry" in sys.argv
    px, spy = pickle.loads(CACHE.read_bytes())
    con = db.connect()
    vix = con.execute("SELECT close FROM prices_daily WHERE symbol='^VIX' "
                      "ORDER BY date DESC LIMIT 1").fetchone()
    vix_level = vix["close"] if vix else 0
    asof = str(px.index[-1].date())
    if dry:                              # 오늘 진입 후보만 미리보기 (장부 미변경)
        ind = _indicators(px, spy)
        print(f"기준일 {asof} · VIX {vix_level:.1f} · 슬롯 {int(SLOTS*1.5) if vix_level>=25 else SLOTS}")
        for s in STRATS:
            print(f"  [{s}] 진입후보 top{SLOTS}: {_candidates(s, ind, set())[:SLOTS]}")
    else:
        r = process(con, px, spy, vix_level, asof)
        for s, v in r.items():
            print(f"[{s}] 평가 ${v['equity']:,.0f} · 진입 {v['opened']} · 청산 {v['closed']}")
    con.close()
