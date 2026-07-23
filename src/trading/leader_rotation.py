"""주도주 로테이션 — 126일 top10 진입/top30 이탈, 주 1회. US(알파카 페이퍼) + KR(키움 모의).

US는 백테스트 생존 전략(leader_backtest.py +6042%). KR은 동일 규칙이 백테스트에서 **실패**
(-61~-74%, leader_backtest_kr.py)했으나 사용자 결정으로 모의 A/B 실험 가동 — 2주 무인 기간에
'백테스트 예측(US 승/KR 패)이 실거동에서 재현되는가'를 관찰한다(검증 체계 자체의 라이브 검증).

- 신호는 signals 큐 경유(리스크·실전 게이트·체결확인·워치독 공통). ROTATION_ENABLED=1 게이트.
- 슬롯 장부 rotation_slots(시장 구분은 심볼 형태: 6자리 숫자=KR): 자기 보유만 관리, 기존 보유 불가침.
  청산 레이어는 로테이션 보유 제외(자체 이탈규칙). 평가 때 실보유 대사(self-heal).
- 사이징: US ROTATION_SLOT_USD($1,000, 소수주 3자리) / KR ROTATION_SLOT_KRW(₩200만, 정수 주 —
  슬롯보다 비싼 종목은 건너뜀). KR 유니버스는 시총 3천억↑(잡주 배제, 백테스트와 동일).
- KR은 장외 주문이 거부되므로 **장중에만 평가**(장외면 deferred — 다음 점검 주기에 재시도).
- 멱등: rotation-{buy|sell}-{sym}-{ISO주}, 시장별 주간 게이트(last_week_US/KR).

미리보기: python -m src.trading.leader_rotation --dry [--kr]
"""
import hashlib
import os
from datetime import date, datetime

import pandas as pd

from src import db
from src.trading import ensure_tables
from src.trading.brokers import alpaca, kiwoom

ENTER_K, EXIT_K, N_SLOTS = 10, 30, 10
LOOKBACK = 126


def _ensure(con):
    con.execute("CREATE TABLE IF NOT EXISTS rotation_slots ("
                "symbol TEXT PRIMARY KEY, qty REAL, entered_at TEXT, "
                "entry_rank REAL, entry_px REAL)")
    con.execute("CREATE TABLE IF NOT EXISTS rotation_meta (key TEXT PRIMARY KEY, value TEXT)")
    r = con.execute("SELECT value FROM rotation_meta WHERE key='last_week'").fetchone()
    if r:                                              # 구 단일키 → US 키 마이그레이션
        con.execute("INSERT OR IGNORE INTO rotation_meta (key, value) "
                    "VALUES ('last_week_US', ?)", (r["value"],))
        con.execute("DELETE FROM rotation_meta WHERE key='last_week'")


def _is_kr(sym) -> bool:
    return str(sym).isdigit()


def _ranks(con, market: str):
    """(126일 수익률 순위 Series, 최근종가 Series) — 데이터 부족 시 None."""
    if market == "KR":                                 # 시총 3천억↑ (백테스트 유니버스와 동일)
        q = ("SELECT p.symbol, p.date, p.close FROM prices_daily p "
             "JOIN stock_meta s ON s.symbol = p.symbol "
             "JOIN sector_map m ON m.stock_code = p.symbol AND m.market='KR' "
             "WHERE p.market='KR' AND s.mcap >= 3e11 AND p.date >= "
             "date((SELECT MAX(date) FROM prices_daily WHERE market='KR'), '-420 days')")
    else:
        q = ("SELECT symbol, date, close FROM prices_daily WHERE market='US_STOCK' AND date >= "
             "date((SELECT MAX(date) FROM prices_daily WHERE market='US_STOCK'), '-210 days')")
    df = pd.read_sql_query(q, con)
    if df.empty:
        return None
    px = df.pivot(index="date", columns="symbol", values="close").sort_index().ffill()
    if len(px) < LOOKBACK + 1:
        return None
    mom = px.iloc[-1] / px.iloc[-(LOOKBACK + 1)] - 1
    return mom.rank(ascending=False), px.iloc[-1]


def _live_symbols(market: str):
    """실보유 심볼 집합 (self-heal 대사용). 조회 불가면 None → 대사 생략."""
    try:
        if market == "KR":
            if not kiwoom.configured():
                return None
            bal = kiwoom.KiwoomBroker().account_balance()
            return {h["code"] for h in (bal or {}).get("holdings", []) if h["qty"] > 0}
        if not alpaca.configured():
            return None
        return {p["symbol"] for p in alpaca.AlpacaBroker().get_positions()}
    except Exception:
        return None


def _slot_qty(market: str, px: float):
    if market == "KR":
        return int(float(os.getenv("ROTATION_SLOT_KRW", "2000000")) // px)
    qty = round(float(os.getenv("ROTATION_SLOT_USD", "1000")) / px, 3)
    return qty if qty > 0 else 0


def _emit(con, sym, action, qty, note, wk):
    h = (f"rotation-{action}-{sym}-{wk}-"
         + hashlib.sha256(f"{sym}{action}{wk}".encode()).hexdigest()[:8])
    con.execute(
        "INSERT OR IGNORE INTO signals "
        "(hash, received_at, source, ticker, action, qty, strategy, raw, status) "
        "VALUES (?,?,?,?,?,?,?,?, 'new')",
        (h, datetime.now().isoformat(timespec="seconds"), "rotation", sym, action,
         qty, note[:60], "{}"))


def evaluate(con=None, dry=False, market: str = "US") -> dict | None:
    """시장별 주 1회 로테이션 평가 → 진입/이탈 신호 emit. dry면 무엇을 할지만."""
    own = con is None
    if own:
        con = db.connect()
    ensure_tables(con)
    _ensure(con)
    iso = date.today().isocalendar()
    wk = f"{iso[0]}-W{iso[1]:02d}"
    out = {"market": market, "week": wk, "enters": [], "exits": [], "top10": []}
    try:
        if market == "KR" and not dry and not kiwoom.KiwoomBroker().is_market_open("000000"):
            return {**out, "deferred": True}           # 장외 — 다음 점검 주기에 재시도
        if not dry:
            last = con.execute("SELECT value FROM rotation_meta WHERE key=?",
                               (f"last_week_{market}",)).fetchone()
            if last and last["value"] == wk:
                return {**out, "skipped": True}

        r = _ranks(con, market)
        if r is None:
            return {**out, "error": "가격 데이터 부족"}
        ranks, last_px = r
        out["top10"] = list(ranks.sort_values().head(10).index)

        slots = {s["symbol"]: dict(s) for s in con.execute("SELECT * FROM rotation_slots")
                 if _is_kr(s["symbol"]) == (market == "KR")}
        if not dry and slots:
            live = _live_symbols(market)               # self-heal: 실보유와 대사
            if live is not None:
                for sym in [s for s in slots if s not in live]:
                    con.execute("DELETE FROM rotation_slots WHERE symbol=?", (sym,))
                    del slots[sym]

        for sym, s in list(slots.items()):             # 이탈: rank > EXIT_K 또는 순위 소멸
            rk = ranks.get(sym)
            if rk is None or pd.isna(rk) or rk > EXIT_K:
                out["exits"].append({"symbol": sym, "qty": s["qty"],
                                     "rank": None if rk is None or pd.isna(rk) else int(rk)})
                if not dry:
                    _emit(con, sym, "sell", s["qty"],
                          f"로테이션 이탈 rank {int(rk) if rk == rk and rk is not None else '소멸'}",
                          wk)
                    con.execute("DELETE FROM rotation_slots WHERE symbol=?", (sym,))
                del slots[sym]

        for sym in ranks.sort_values().index:          # 진입: 빈 슬롯을 top ENTER_K로
            if len(slots) >= N_SLOTS or ranks[sym] > ENTER_K:
                break
            if sym in slots:
                continue
            px = float(last_px.get(sym) or 0)
            qty = _slot_qty(market, px) if px > 0 else 0
            if qty <= 0:
                continue                                # KR: 슬롯보다 비싼 종목은 건너뜀
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
            con.execute("INSERT OR REPLACE INTO rotation_meta (key, value) VALUES (?, ?)",
                        (f"last_week_{market}", wk))
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
    mk = "KR" if "--kr" in sys.argv else "US"
    res = evaluate(dry="--live" not in sys.argv, market=mk)
    print(f"주도주 로테이션 [{mk}] ({res['week']}{', DRY' if '--live' not in sys.argv else ''})")
    print("  top10:", " · ".join(str(s) for s in res.get("top10", [])))
    for e in res.get("enters", []):
        print(f"  진입: {e['symbol']} x{e['qty']:g} (rank {e['rank']}, {e['px']:,.0f})")
    for e in res.get("exits", []):
        print(f"  이탈: {e['symbol']} x{e['qty']:g} (rank {e['rank']})")
    if res.get("deferred"):
        print("  장외 — 장중에 재평가")
    elif not res.get("enters") and not res.get("exits"):
        print("  변경 없음")
