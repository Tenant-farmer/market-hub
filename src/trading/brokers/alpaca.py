"""Alpaca 어댑터 — 페이퍼 REST v2. 실전 전환 시 BASE만 교체.

- client_order_id로 멱등: 같은 신호 재제출 시 Alpaca가 422 → 기존 주문 조회로 대체
- 크립토 심볼 매핑 (TV는 BTCUSD, Alpaca는 BTC/USD) + 크립토는 GTC/24시간
"""
import os
from datetime import datetime

import requests

from src.trading.brokers.base import BrokerAdapter, OrderRequest

BASE = "https://paper-api.alpaca.markets"
CRYPTO_MAP = {"BTCUSD": "BTC/USD", "ETHUSD": "ETH/USD", "SOLUSD": "SOL/USD"}


def _headers():
    return {
        "APCA-API-KEY-ID": os.getenv("ALPACA_API_KEY", ""),
        "APCA-API-SECRET-KEY": os.getenv("ALPACA_API_SECRET", ""),
    }


def configured() -> bool:
    return bool(os.getenv("ALPACA_API_KEY")) and bool(os.getenv("ALPACA_API_SECRET"))


class AlpacaBroker(BrokerAdapter):
    name = "alpaca"

    def submit_order(self, con, req: OrderRequest, client_order_id: str,
                     signal_id: int | None = None) -> dict:
        sym = CRYPTO_MAP.get(req.ticker, req.ticker)
        body = {
            "symbol": sym, "side": req.action, "type": "market",
            "time_in_force": "gtc" if "/" in sym else "day",
            "qty": str(req.qty if req.qty else 1),
            "client_order_id": client_order_id,
        }
        dup, ok, status, msg = False, False, "error", ""
        try:
            r = requests.post(f"{BASE}/v2/orders", json=body, headers=_headers(), timeout=15)
            if r.status_code == 422 and "client_order_id" in r.text:
                dup = True
                r = requests.get(
                    f"{BASE}/v2/orders:by_client_order_id",
                    params={"client_order_id": client_order_id},
                    headers=_headers(), timeout=15,
                )
            data = r.json() if r.content else {}
            ok = r.ok
            status = data.get("status") or f"http_{r.status_code}"
            msg = data.get("id") or str(data.get("message", ""))[:150]
        except Exception as e:
            msg = str(e)[:150]

        con.execute(
            "INSERT OR IGNORE INTO orders "
            "(signal_id, client_order_id, broker, ticker, action, qty, price, status, created_at, message) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                signal_id, client_order_id, self.name, sym, req.action,
                req.qty, req.price, status,
                datetime.now().isoformat(timespec="seconds"), msg,
            ),
        )
        con.execute(
            "UPDATE orders SET status=?, message=? WHERE client_order_id=?",
            (status, msg, client_order_id),
        )
        con.commit()
        return {"ok": ok, "dup": dup, "status": status}

    def order_status(self, client_order_id: str) -> dict:
        r = requests.get(
            f"{BASE}/v2/orders:by_client_order_id",
            params={"client_order_id": client_order_id}, headers=_headers(), timeout=15,
        )
        return r.json() if r.ok else {"status": f"http_{r.status_code}"}

    def get_account(self) -> dict:
        r = requests.get(f"{BASE}/v2/account", headers=_headers(), timeout=15)
        return r.json() if r.ok else {}

    def get_positions(self) -> list:
        r = requests.get(f"{BASE}/v2/positions", headers=_headers(), timeout=15)
        return r.json() if r.ok else []

    def is_market_open(self, ticker: str) -> bool:
        if "/" in CRYPTO_MAP.get(ticker, ticker):
            return True   # 크립토 24/7
        r = requests.get(f"{BASE}/v2/clock", headers=_headers(), timeout=15)
        return bool(r.ok and r.json().get("is_open"))
