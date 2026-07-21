"""브로커 어댑터 공통 계약 — paper_log/alpaca/kiwoom이 같은 모양으로 구현한다."""
from dataclasses import dataclass


@dataclass
class OrderRequest:
    ticker: str            # US 티커 또는 KR 6자리 코드
    action: str            # buy | sell
    qty: float | None = None
    price: float | None = None   # None = 시장가 의도
    strategy: str = ""


class BrokerAdapter:
    """서브클래스가 구현할 인터페이스. paper_log 단계에선 submit_order만 실사용."""

    name = "base"

    def submit_order(self, con, req: OrderRequest, client_order_id: str,
                     signal_id: int | None = None) -> dict:
        raise NotImplementedError

    def get_account(self) -> dict:
        raise NotImplementedError

    def get_positions(self) -> list:
        raise NotImplementedError

    def is_market_open(self, ticker: str) -> bool:
        raise NotImplementedError
