"""자동매매 포지션·손익 페이지 (/positions) — KR(키움 모의) + US(Alpaca) 통합.

실전 게이트 상태 + 브로커별 보유종목 평가손익 + 최근 주문. 브로커 API 온디맨드 조회.
"""
import os
from datetime import date

from flask import Blueprint, render_template

from src import db
from src.trading import ensure_tables, risk, state
from src.trading.brokers import alpaca, kiwoom

bp = Blueprint("positions", __name__)


def _num(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _alpaca_view():
    """Alpaca 페이퍼 계좌 + 포지션 (USD). 실패 시 None."""
    if not alpaca.configured():
        return None
    try:
        br = alpaca.AlpacaBroker()
        acct = br.get_account()
        pos = br.get_positions()
        holdings = [
            {
                "code": p.get("symbol"), "name": p.get("symbol"),
                "qty": _num(p.get("qty")), "avg": _num(p.get("avg_entry_price")),
                "cur": _num(p.get("current_price")), "value": _num(p.get("market_value")),
                "pl": _num(p.get("unrealized_pl")), "plpc": _num(p.get("unrealized_plpc")) * 100,
            }
            for p in (pos if isinstance(pos, list) else [])
        ]
        return {
            "cash": _num(acct.get("cash")), "equity": _num(acct.get("equity")),
            "bp": _num(acct.get("buying_power")),
            "acct_no": acct.get("account_number", ""),
            "holdings": holdings,
        }
    except Exception:
        return None


@bp.get("/positions")
def positions():
    con = db.connect()
    ensure_tables(con)
    st = state.get_state(con)
    today = date.today().isoformat()
    orders_today = con.execute(
        "SELECT COUNT(*) c FROM orders WHERE substr(created_at,1,10)=?", (today,)
    ).fetchone()["c"]
    recent_orders = con.execute(
        "SELECT created_at, broker, ticker, action, qty, price, status, message "
        "FROM orders ORDER BY id DESC LIMIT 15"
    ).fetchall()
    con.close()

    gate = {
        "mode": st["mode"], "armed": st["armed"],
        "live_hot": st["mode"] == "live" and st["armed"],
        "kill": os.getenv("KILL_SWITCH") == "1",
        "max_usd": risk._f("MAX_ORDER_USD", risk.MAX_ORDER_USD),
        "max_krw": risk._f("MAX_ORDER_KRW", risk.MAX_ORDER_KRW),
        "max_daily": int(risk._f("MAX_DAILY_ORDERS", risk.MAX_DAILY_ORDERS)),
        "orders_today": orders_today,
    }
    kr = kiwoom.KiwoomBroker().account_balance() if kiwoom.configured() else None
    kr_mock = kiwoom.is_mock() if kiwoom.configured() else True
    us = _alpaca_view()

    return render_template(
        "positions.html", gate=gate, kr=kr, kr_mock=kr_mock, us=us,
        recent_orders=[dict(r) for r in recent_orders],
    )
