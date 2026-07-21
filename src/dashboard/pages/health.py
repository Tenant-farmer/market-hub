"""수집기 상태 + 자동매매 실전 게이트 상태 페이지."""
import os
from datetime import date

from flask import Blueprint, render_template

from src import db
from src.trading import ensure_tables, risk, state

bp = Blueprint("health", __name__)


@bp.get("/health")
def health():
    con = db.connect()
    runs = con.execute("SELECT * FROM collector_runs ORDER BY id DESC LIMIT 30").fetchall()
    ensure_tables(con)
    st = state.get_state(con)
    today = date.today().isoformat()
    orders_today = con.execute(
        "SELECT COUNT(*) c FROM orders WHERE substr(created_at,1,10)=?", (today,)
    ).fetchone()["c"]
    recent_orders = con.execute(
        "SELECT created_at, broker, ticker, action, qty, status FROM orders ORDER BY id DESC LIMIT 8"
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
    return render_template(
        "health.html", runs=[dict(r) for r in runs],
        gate=gate, recent_orders=[dict(r) for r in recent_orders],
    )
