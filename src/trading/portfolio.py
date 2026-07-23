"""계좌 에쿼티 일별 스냅샷 — 무인 가동 관찰성 (에쿼티 곡선의 원천).

hourly가 매 실행마다 upsert → 하루 마지막 실행 값이 EOD 근사로 남는다.
/positions 추이 차트가 읽음. 수동 실행: python -m src.trading.portfolio
"""
from datetime import date

from src.trading.brokers import alpaca, kiwoom


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
        except Exception:
            pass
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
