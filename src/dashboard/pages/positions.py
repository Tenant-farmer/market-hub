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


def _tagger(con):
    """보유 종목 → 전략 귀속 (로테이션/신호진입/기본보유/수동)."""
    try:
        rot = {str(r["symbol"]) for r in con.execute("SELECT symbol FROM rotation_slots")}
    except Exception:
        rot = set()
    sig = {"SPY", os.getenv("SIGNAL_ENTRY_SYMBOL", "SPY"),
           os.getenv("SIGNAL_ENTRY_SYMBOL_KR", "069500")} - {""}
    base_hold = {"005930", "010950", "AAPL"}

    def tag(code):
        if code in rot:
            return "로테이션"
        if code in sig:
            return "신호진입"
        if code in base_hold:
            return "기본보유"
        return "수동"
    return tag


def _spark(vals, w=240, h=44):
    """값 목록 → 인라인 SVG polyline 좌표 (없으면 None)."""
    v = [x for x in vals if x is not None]
    if len(v) < 2:
        return None
    lo, hi = min(v), max(v)
    span = (hi - lo) or 1
    step = w / (len(v) - 1)
    return " ".join(f"{i * step:.1f},{h - 3 - (x - lo) / span * (h - 6):.1f}"
                    for i, x in enumerate(v))


def _trend(con):
    """portfolio_snapshots + 환율 → 일별 원화 합산 시리즈."""
    try:
        rows = con.execute("SELECT date, broker, equity, pl FROM portfolio_snapshots "
                           "ORDER BY date").fetchall()
    except Exception:
        return None
    if not rows:
        return None
    fx = {r["date"]: r["close"] for r in con.execute(
        "SELECT date, close FROM prices_daily WHERE symbol='KRW=X' ORDER BY date")}
    fx_dates = sorted(fx)
    by_date = {}
    for r in rows:
        by_date.setdefault(r["date"], {})[r["broker"]] = r
    days = []
    last_rate = None
    for d in sorted(by_date):
        rate = next((fx[x] for x in reversed(fx_dates) if x <= d), None) or last_rate
        last_rate = rate or last_rate
        kr = by_date[d].get("kiwoom")
        us = by_date[d].get("alpaca")
        total = (kr["equity"] if kr else 0) + (us["equity"] * rate if us and rate else 0)
        days.append({"date": d, "kr": kr["equity"] if kr else None,
                     "us": us["equity"] if us else None,
                     "total": total or None,
                     "pl": ((kr["pl"] if kr else 0) + (us["pl"] * rate if us and rate else 0))})
    return {
        "days": days, "rate": last_rate,
        "spark": _spark([d["total"] for d in days]),
        "first": next((d for d in days if d["total"]), None), "last": days[-1],
    }


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
    tag = _tagger(con)
    trend = _trend(con)
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
    for h in (kr or {}).get("holdings", []) + (us or {}).get("holdings", []):
        h["tag"] = tag(h["code"])
    # 전략별 합계 (원화 환산 — 환율 없으면 US분 생략)
    rate = (trend or {}).get("rate")
    strat = {}
    for h in (kr or {}).get("holdings", []):
        s = strat.setdefault(h["tag"] + " KR", {"value": 0, "pl": 0})
        s["value"] += h["value"]; s["pl"] += h["pl"]
    for h in (us or {}).get("holdings", []):
        if rate:
            s = strat.setdefault(h["tag"] + " US", {"value": 0, "pl": 0})
            s["value"] += h["value"] * rate; s["pl"] += h["pl"] * rate

    return render_template(
        "positions.html", gate=gate, kr=kr, kr_mock=kr_mock, us=us,
        recent_orders=[dict(r) for r in recent_orders],
        trend=trend, strat=strat,
    )
