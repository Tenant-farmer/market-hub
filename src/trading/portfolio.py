"""계좌 에쿼티 일별 스냅샷 — 무인 가동 관찰성 (에쿼티 곡선의 원천).

hourly가 매 실행마다 upsert → 하루 마지막 실행 값이 EOD 근사로 남는다.
/positions 추이 차트가 읽음. 수동 실행: python -m src.trading.portfolio
"""
from datetime import date

from src.trading.brokers import alpaca, kiwoom
from src.errlog import swallow


def ensure(con):
    con.execute("CREATE TABLE IF NOT EXISTS portfolio_snapshots ("
                "date TEXT, broker TEXT, equity REAL, cash REAL, pl REAL, "
                "PRIMARY KEY (date, broker))")


def snapshot(con) -> int:
    """브로커별 (총자산, 현금, 미실현손익) upsert. 반환: 기록한 브로커 수.

    **네트워크를 먼저 다 끝내고 그다음에 쓴다.** 원래는 키움 INSERT로 쓰기 트랜잭션을
    연 뒤 Alpaca API를 2번 부르고 나서야 커밋해, 락을 **0.4~5초** 쥐고 있었다
    (키움 `_throttle()` 1초 + 레이트리밋 재시도 1.2초×2까지 겹치면 더 길다).
    그게 엔진이 :05마다 `database is locked`로 넘어지던 진짜 원인이다 —
    대량 INSERT가 느린 게 아니었다(5,300행 executemany+commit 실측 **3ms**).
    지금 구조에서 락 점유는 쓰기 몇 ms뿐이다.
    """
    ensure(con)
    today = date.today().isoformat()
    rows = []
    if kiwoom.configured():
        b = kiwoom.KiwoomBroker().account_balance()
        if b and b["cash"]:                # 추정예탁자산 = 현금 + 평가 = 총자산
            rows.append((today, "kiwoom", b["cash"], b["cash"] - b["value"], b["pl"]))
    if alpaca.configured():
        try:
            br = alpaca.AlpacaBroker()
            a = br.get_account()
            eq = float(a.get("equity") or 0)
            if eq:
                pl = sum(float(p.get("unrealized_pl") or 0) for p in br.get_positions())
                rows.append((today, "alpaca", eq, float(a.get("cash") or 0), pl))
        except Exception as e:
            swallow("portfolio.snapshot", e)
    if rows:                               # 여기서부터 몇 ms — 네트워크는 이미 끝났다
        con.executemany("INSERT OR REPLACE INTO portfolio_snapshots VALUES (?,?,?,?,?)", rows)
        con.commit()
    return len(rows)


if __name__ == "__main__":
    import sys

    from dotenv import load_dotenv

    load_dotenv()
    sys.path.insert(0, ".")
    from src import db

    c = db.connect()
    print("스냅샷:", snapshot(c), "브로커")
    c.commit()
    for r in c.execute("SELECT * FROM portfolio_snapshots ORDER BY date DESC, broker LIMIT 6"):
        print(f"  {r['date']} {r['broker']:8} 총자산 {r['equity']:,.0f}  현금 {r['cash']:,.0f}  "
              f"미실현 {r['pl']:+,.0f}")
    c.close()


def equity_curve(con, broker: str, trading_days_only: bool = True) -> list[tuple[str, float]]:
    """(날짜, 평가액) 시계열 — **통계용 정본 조회**.

    스냅샷은 매시간 돌아 **주말·공휴일에도 행이 생긴다**. 장이 안 열린 날은 직전 영업일
    값이 그대로 복사돼 **수익률 0%인 가짜 관측**이 된다(2026-07-25·26 실측 +0.000%).
    그 0% 날들이 변동성을 낮춰 **VaR를 실제보다 작게, α의 t값을 실제보다 크게** 만든다 —
    둘 다 '위험은 작고 실력은 있다'는 쪽으로 틀리므로 통계에 쓸 땐 반드시 제외한다.

    표시용(차트에 평평한 주말 구간을 그대로 보여주는 것)은 정직하므로
    trading_days_only=False로 쓰면 된다. 공휴일은 여기서 못 거르지만, 그날도 값이
    복사되므로 남은 0% 관측은 소수다(거래일 필터가 대부분을 걷어낸다).
    """
    ensure(con)
    q = ("SELECT date, equity FROM portfolio_snapshots "
         "WHERE broker=? AND equity IS NOT NULL AND equity > 0")
    if trading_days_only:
        q += " AND CAST(strftime('%w', date) AS INTEGER) BETWEEN 1 AND 5"
    return [(r["date"], r["equity"]) for r in con.execute(q + " ORDER BY date", (broker,))]


def pnl_summary(con) -> dict | None:
    """오늘·누적 손익 요약 — 매매 알림에 붙일 맥락. 스냅샷이 2일 미만이면 None.

    에쿼티(총자산) 변화로 잰다. **입출금이 있으면 손익과 어긋난다** — 무인 검증 기간엔
    입출금이 없어 유효하지만, 실전 전환 시 입출금 이력을 빼는 보정이 필요하다.
    원화 환산은 KRW=X 최근 종가 사용(환율 자체의 변동도 섞인다는 한계 존재).
    """
    ensure(con)
    rows = con.execute("SELECT date, broker, equity, pl FROM portfolio_snapshots "
                       "ORDER BY date").fetchall()
    if not rows:
        return None
    fx = con.execute("SELECT close FROM prices_daily WHERE symbol='KRW=X' "
                     "ORDER BY date DESC LIMIT 1").fetchone()
    rate = fx["close"] if fx else None

    def _krw(broker, v):
        if broker == "kiwoom":
            return v
        return v * rate if rate else 0.0

    by_date: dict = {}
    for r in rows:
        d = by_date.setdefault(r["date"], {"equity": 0.0, "pl": 0.0})
        d["equity"] += _krw(r["broker"], r["equity"])
        d["pl"] += _krw(r["broker"], r["pl"] or 0)
    dates = sorted(by_date)
    if len(dates) < 2:
        return None
    cur, first, prev = by_date[dates[-1]], by_date[dates[0]], by_date[dates[-2]]

    def _chg(a, b):
        return (a - b, (a / b - 1) * 100 if b else 0.0)

    d_amt, d_pct = _chg(cur["equity"], prev["equity"])
    t_amt, t_pct = _chg(cur["equity"], first["equity"])
    return {
        "equity": cur["equity"], "unrealized": cur["pl"],
        "day_amt": d_amt, "day_pct": d_pct,
        "total_amt": t_amt, "total_pct": t_pct,
        "since": dates[0], "days": len(dates),
    }
