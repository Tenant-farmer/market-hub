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
    """브로커별 (총자산, 현금, 미실현손익) upsert. 반환: 기록한 브로커 수."""
    ensure(con)
    today = date.today().isoformat()
    n = 0
    if kiwoom.configured():
        b = kiwoom.KiwoomBroker().account_balance()
        if b and b["cash"]:                # 추정예탁자산 = 현금 + 평가 = 총자산
            con.execute("INSERT OR REPLACE INTO portfolio_snapshots VALUES (?,?,?,?,?)",
                        (today, "kiwoom", b["cash"], b["cash"] - b["value"], b["pl"]))
            n += 1
    if alpaca.configured():
        try:
            br = alpaca.AlpacaBroker()
            a = br.get_account()
            eq = float(a.get("equity") or 0)
            if eq:
                pl = sum(float(p.get("unrealized_pl") or 0) for p in br.get_positions())
                con.execute("INSERT OR REPLACE INTO portfolio_snapshots VALUES (?,?,?,?,?)",
                            (today, "alpaca", eq, float(a.get("cash") or 0), pl))
                n += 1
        except Exception as e:
            swallow("portfolio.snapshot", e)
    return n


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
