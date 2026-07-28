"""실계좌 평가액 일별 스냅샷 — 2차 판정의 α 계산에 필요한 **에쿼티 곡선**.

왜 생겼나 (2026-07-28): 확인해보니 에쿼티 곡선이 **가상장부에만** 있었다
(`daytrade_equity`). 2차 판정(~8/20)의 'α > 0 (시장 초과수익)' 항목은 곡선이 있어야
계산되는데, 정작 **실제 매매 시스템은 곡선이 없어 영영 판정할 수 없는 상태**였다.
보유 종목의 손익률은 각자 진입 시점 기준이라 기간 수익률로 쓸 수 없다 —
벤치마크와 나란히 놓으려면 매일 같은 시각의 평가액 계열이 필요하다.

안 남긴 과거는 되살릴 수 없다. 그래서 즉시 시작한다.

설계
- 하루 1회 **16시 슬롯**(KR 마감 15:30 이후). 수익률 계열은 매일 같은 시각이어야 성립한다.
  이 시각 Alpaca는 직전 미국장 종가(05시 KST 마감) 기준이라 역시 일관적이다.
- 브로커별로 **자국 통화 그대로** 저장. 환율로 합치면 환변동이 성과로 오염된다.
  수익률은 브로커 안에서 계산하므로 환산이 필요 없다.
- 조회 실패한 브로커는 **그 행을 안 남긴다**(0으로 채우면 -100% 하락으로 보인다).

수동: python -m src.jobs.account_equity
"""
from datetime import date


def ensure_table(con) -> None:
    con.execute("""CREATE TABLE IF NOT EXISTS account_equity (
        date TEXT NOT NULL, broker TEXT NOT NULL, equity REAL, cash REAL,
        n_pos INTEGER, currency TEXT,
        PRIMARY KEY (date, broker))""")


def _kiwoom():
    from src.trading.brokers import kiwoom

    if not kiwoom.configured():
        return None
    v = kiwoom.KiwoomBroker().account_balance()
    if not v:
        return None
    # 어댑터의 'cash'는 키움 원장의 **추정예탁자산금액**(prsm_dpst_aset_amt)이라
    # 이미 보유 평가분을 포함한 **계좌 총액**이다 — 'value'를 더하면 이중계상된다.
    # 2026-07-28 실측: 예탁 497,006,068 (원금 5억 − 손실·수수료) 안에
    # 보유평가 16,612,250이 들어 있다. 그래서 equity=cash, 순현금=cash−value.
    total = v.get("cash") or 0
    return {"equity": total, "cash": total - (v.get("value") or 0),
            "n_pos": len(v.get("holdings", [])), "currency": "KRW"}


def _alpaca():
    from src.dashboard.pages.positions import _alpaca_view

    v = _alpaca_view()
    if not v:
        return None
    return {"equity": v.get("equity"), "cash": v.get("cash"),
            "n_pos": len(v.get("holdings", [])), "currency": "USD"}


def snapshot(con, asof: str | None = None) -> int:
    """오늘자 브로커별 평가액 기록. 반환: 기록한 행 수."""
    ensure_table(con)
    asof = asof or date.today().isoformat()
    n = 0
    for broker, fn in (("kiwoom", _kiwoom), ("alpaca", _alpaca)):
        try:
            v = fn()
        except Exception:
            v = None
        if not v or v.get("equity") in (None, 0):
            continue                      # 실패·미설정은 건너뛴다 (0을 넣으면 폭락으로 보인다)
        con.execute(
            "INSERT OR REPLACE INTO account_equity "
            "(date, broker, equity, cash, n_pos, currency) VALUES (?,?,?,?,?,?)",
            (asof, broker, float(v["equity"]), float(v.get("cash") or 0),
             int(v.get("n_pos") or 0), v["currency"]))
        n += 1
    con.commit()
    return n


def curve(con, broker: str) -> list[tuple[str, float]]:
    """(날짜, 평가액) 시계열 — α/β 계산용."""
    ensure_table(con)
    return [(r["date"], r["equity"]) for r in con.execute(
        "SELECT date, equity FROM account_equity WHERE broker=? AND equity > 0 "
        "ORDER BY date", (broker,))]


if __name__ == "__main__":
    import sys
    from pathlib import Path

    from dotenv import load_dotenv

    ROOT = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(ROOT))
    load_dotenv(ROOT / ".env")
    from src import db

    c = db.connect()
    print(f"기록 {snapshot(c)}건")
    for b in ("kiwoom", "alpaca"):
        print(f"  {b}: {curve(c, b)}")
    c.close()
