"""주도주 로테이션 (126일) — 백테스트에서 생존한 선정 전략의 페이퍼 실거동 모듈.

scripts/leader_backtest.py 검증 규칙 그대로: **126일 수익률 순위 top10 진입 / top30 이탈 매도**,
주(ISO week) 1회 평가. 신호는 signals 큐 경유 → 리스크·실전 게이트(paper)·체결확인·워치독 공통
파이프라인이 처리. 게이트: ROTATION_ENABLED=1 일 때만 워커가 실행(기본 off).

- 슬롯 장부 rotation_slots: 이 모듈이 관리하는 보유만 기록(기존 보유 AAPL·BTC 등은 불가침).
  청산 레이어(exits)는 로테이션 보유를 제외 — 이탈 규칙(rank>30)이 자체 청산.
- self-heal: 평가 때 실제 Alpaca 보유와 대사, 외부에서 사라진 슬롯은 장부에서 제거.
- 사이징: ROTATION_SLOT_USD(기본 1000)/최근종가, 소수 3자리(Alpaca 소수주).
- 멱등: rotation-{buy|sell}-{sym}-{ISO주} — 같은 주 재실행돼도 중복 없음.

미리보기: python -m src.trading.leader_rotation --dry
"""
import hashlib
import os
from datetime import date, datetime

import pandas as pd

from src import db
from src.trading import ensure_tables
from src.trading.brokers import alpaca

ENTER_K, EXIT_K, N_SLOTS = 10, 30, 10
LOOKBACK = 126


def _ensure(con):
    con.execute("CREATE TABLE IF NOT EXISTS rotation_slots ("
                "symbol TEXT PRIMARY KEY, qty REAL, entered_at TEXT, "
                "entry_rank REAL, entry_px REAL)")
    con.execute("CREATE TABLE IF NOT EXISTS rotation_meta (key TEXT PRIMARY KEY, value TEXT)")


def _ranks(con):
    """(126일 수익률 순위 Series, 최근종가 Series) — 데이터 부족 시 None."""
    df = pd.read_sql_query(
        "SELECT symbol, date, close FROM prices_daily WHERE market='US_STOCK' AND date >= "
        "date((SELECT MAX(date) FROM prices_daily WHERE market='US_STOCK'), '-210 days')", con)
    if df.empty:
        return None
    px = df.pivot(index="date", columns="symbol", values="close").sort_index().ffill()
    if len(px) < LOOKBACK + 1:
        return None
    mom = px.iloc[-1] / px.iloc[-(LOOKBACK + 1)] - 1
    return mom.rank(ascending=False), px.iloc[-1]


def _emit(con, sym, action, qty, note, wk):
    h = (f"rotation-{action}-{sym}-{wk}-"
         + hashlib.sha256(f"{sym}{action}{wk}".encode()).hexdigest()[:8])
    con.execute(
        "INSERT OR IGNORE INTO signals "
        "(hash, received_at, source, ticker, action, qty, strategy, raw, status) "
        "VALUES (?,?,?,?,?,?,?,?, 'new')",
        (h, datetime.now().isoformat(timespec="seconds"), "rotation", sym, action,
         qty, note[:60], "{}"))


def evaluate(con=None, dry=False) -> dict | None:
    """주 1회 로테이션 평가 → 진입/이탈 신호 emit. dry면 무엇을 할지만 반환."""
    own = con is None
    if own:
        con = db.connect()
    ensure_tables(con)
    _ensure(con)
    iso = date.today().isocalendar()
    wk = f"{iso[0]}-W{iso[1]:02d}"
    out = {"week": wk, "enters": [], "exits": [], "top10": []}
    try:
        if not dry:
            last = con.execute("SELECT value FROM rotation_meta WHERE key='last_week'").fetchone()
            if last and last["value"] == wk:
                return {"week": wk, "skipped": True, "enters": [], "exits": []}

        r = _ranks(con)
        if r is None:
            return {"week": wk, "error": "가격 데이터 부족", "enters": [], "exits": []}
        ranks, last_px = r
        out["top10"] = list(ranks.sort_values().head(10).index)

        slots = {s["symbol"]: dict(s) for s in con.execute("SELECT * FROM rotation_slots")}
        if not dry and slots and alpaca.configured():          # self-heal: 실보유와 대사
            try:
                live = {p["symbol"] for p in alpaca.AlpacaBroker().get_positions()}
                for sym in [s for s in slots if s not in live]:
                    con.execute("DELETE FROM rotation_slots WHERE symbol=?", (sym,))
                    del slots[sym]
            except Exception:
                pass

        for sym, s in list(slots.items()):                     # 이탈: rank > EXIT_K 또는 순위 소멸
            rk = ranks.get(sym)
            if rk is None or pd.isna(rk) or rk > EXIT_K:
                out["exits"].append({"symbol": sym, "qty": s["qty"],
                                     "rank": None if rk is None or pd.isna(rk) else int(rk)})
                if not dry:
                    _emit(con, sym, "sell", s["qty"],
                          f"로테이션 이탈 rank {int(rk) if rk == rk and rk is not None else '소멸'}", wk)
                    con.execute("DELETE FROM rotation_slots WHERE symbol=?", (sym,))
                del slots[sym]

        slot_usd = float(os.getenv("ROTATION_SLOT_USD", "1000"))
        for sym in ranks.sort_values().index:                  # 진입: 빈 슬롯을 top ENTER_K로
            if len(slots) >= N_SLOTS or ranks[sym] > ENTER_K:
                break
            if sym in slots:
                continue
            px = float(last_px.get(sym) or 0)
            if px <= 0:
                continue
            qty = round(slot_usd / px, 3)
            if qty <= 0:
                continue
            out["enters"].append({"symbol": sym, "qty": qty, "rank": int(ranks[sym]), "px": px})
            if not dry:
                _emit(con, sym, "buy", qty, f"로테이션 진입 rank {int(ranks[sym])}", wk)
                con.execute(
                    "INSERT OR REPLACE INTO rotation_slots "
                    "(symbol, qty, entered_at, entry_rank, entry_px) VALUES (?,?,?,?,?)",
                    (sym, qty, datetime.now().isoformat(timespec="seconds"),
                     float(ranks[sym]), px))
            slots[sym] = {"qty": qty}

        if not dry:
            con.execute("INSERT OR REPLACE INTO rotation_meta (key, value) "
                        "VALUES ('last_week', ?)", (wk,))
            con.commit()
        out["slots"] = len(slots)
        return out
    finally:
        if own:
            con.close()


if __name__ == "__main__":
    import sys

    from dotenv import load_dotenv

    load_dotenv()
    res = evaluate(dry="--live" not in sys.argv)
    print(f"주도주 로테이션 ({res['week']}{', DRY' if '--live' not in sys.argv else ''})")
    print("  top10:", " · ".join(res.get("top10", [])))
    for e in res.get("enters", []):
        print(f"  진입: {e['symbol']} x{e['qty']:g} (rank {e['rank']}, ${e['px']:,.2f})")
    for e in res.get("exits", []):
        print(f"  이탈: {e['symbol']} x{e['qty']:g} (rank {e['rank']})")
    if not res.get("enters") and not res.get("exits"):
        print("  변경 없음")
