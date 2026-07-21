"""paper_log 어댑터 — 주문을 내지 않고 '주문 의도'만 orders 테이블에 기록.

브로커 키 없이 웹훅→엔진→주문 배관 전체를 검증하는 용도.
client_order_id UNIQUE라 같은 신호가 다시 와도 중복 기록되지 않는다 (멱등).
"""
from datetime import datetime

from src.trading.brokers.base import BrokerAdapter, OrderRequest


class PaperLogBroker(BrokerAdapter):
    name = "paper_log"

    def submit_order(self, con, req: OrderRequest, client_order_id: str,
                     signal_id: int | None = None) -> dict:
        cur = con.execute(
            "INSERT OR IGNORE INTO orders "
            "(signal_id, client_order_id, broker, ticker, action, qty, price, status, created_at, message) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                signal_id, client_order_id, self.name, req.ticker, req.action,
                req.qty, req.price, "logged",
                datetime.now().isoformat(timespec="seconds"),
                f"주문 의도 기록 (전략: {req.strategy or '-'})",
            ),
        )
        con.commit()
        return {"ok": True, "dup": cur.rowcount == 0, "status": "logged"}

    def get_account(self) -> dict:
        return {"broker": self.name, "cash": None}

    def get_positions(self) -> list:
        return []

    def is_market_open(self, ticker: str) -> bool:
        return True   # 기록만 하므로 항상 허용
