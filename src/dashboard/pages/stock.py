"""종목 상세 페이지 — US(yfinance) / KR(pykrx+야후 보조) 자동 분기 + 인트라데이 API."""
import time as _time

from flask import Blueprint, jsonify, render_template, request

from src import db, stock_info
from src.dashboard import queries

bp = Blueprint("stock", __name__)

_IV_CACHE: dict = {}   # (symbol, iv) -> (fetched_epoch, rows) — 프로세스 메모리, 30분 TTL
_IV_TTL = 1800


@bp.get("/api/intraday/<symbol>")
def intraday(symbol):
    symbol = symbol.upper()
    iv = request.args.get("iv", "1h")
    if iv not in ("1h", "4h"):
        iv = "1h"
    hit = _IV_CACHE.get((symbol, iv))
    if hit and _time.time() - hit[0] < _IV_TTL:
        return jsonify(hit[1])

    con = db.connect()
    yft = None
    if con.execute(
        "SELECT 1 FROM sector_map WHERE stock_code=? AND market='US_STOCK'", (symbol,)
    ).fetchone():
        yft = symbol
    else:
        kr = con.execute(
            "SELECT sector_code FROM sector_map WHERE stock_code=? AND market='KR'", (symbol,)
        ).fetchone()
        if kr:
            yft = symbol + (".KQ" if kr["sector_code"].startswith("2") else ".KS")
    con.close()
    if not yft:
        return jsonify([])
    try:
        rows = stock_info.intraday_candles(yft, group4=(iv == "4h"))
    except Exception:
        rows = []
    _IV_CACHE[(symbol, iv)] = (_time.time(), rows)
    return jsonify(rows)


def _heat(v):
    if v is None:
        return ""
    a = min(abs(v) / 10, 1) * 0.45
    return (f"rgba(12,163,12,{a:.2f})" if v > 0 else f"rgba(208,59,59,{a:.2f})")


def _monthly_vm(monthly):
    if not monthly:
        return None
    return {
        "avg": [{"v": v, "bg": _heat(v)} for v in monthly["avg"]],
        "years": [
            {"y": yr["y"], "m": [{"v": v, "bg": _heat(v)} for v in yr["m"]]}
            for yr in monthly["years"]
        ],
        "cur": monthly["cur"],
    }


def _candles(con, symbol):
    """최근 캔들 패턴 (참고용 — 실측상 예측력 없음, UI에 성적 함께 표시)."""
    try:
        import pandas as pd

        from src.analytics.candles import latest

        rows = con.execute(
            "SELECT date, open, high, low, close FROM prices_daily WHERE symbol=? "
            "ORDER BY date DESC LIMIT 60", (symbol,)).fetchall()
        if len(rows) < 10:
            return []
        df = pd.DataFrame([dict(r) for r in rows][::-1]).set_index("date")
        return latest(df, days=5)
    except Exception:
        return []


def _our_metrics(con, scope: str, symbol: str):
    rows = con.execute(
        """
        SELECT metric, value FROM analytics_daily
        WHERE scope=? AND code=?
          AND date=(SELECT MAX(date) FROM analytics_daily WHERE scope=?)
        """,
        (scope, symbol, scope),
    ).fetchall()
    return {r["metric"]: r["value"] for r in rows} if rows else None


@bp.get("/stock/<symbol>")
def stock_page(symbol):
    symbol = symbol.upper()
    force = request.args.get("refresh") == "1"
    con = db.connect()

    us = con.execute(
        "SELECT name FROM sector_map WHERE stock_code=? AND market='US_STOCK'", (symbol,)
    ).fetchone()
    if us:
        d = stock_info.get_detail(con, symbol, force=force)
        our = _our_metrics(con, "us_stock", symbol)
        tvrow = con.execute("SELECT tv_symbol FROM stock_meta WHERE symbol=?", (symbol,)).fetchone()
        tv_symbol = tvrow["tv_symbol"] if tvrow and tvrow["tv_symbol"] else symbol
        sym_prices = queries.ohlcv(con, symbol)
        candles = _candles(con, symbol)
        from src.dashboard import stock_extras as _ex

        pead = _ex.pead_context(symbol)
        risk = _ex.risk_profile(con, symbol)
        fin_trend = _ex.financials_trend(symbol)
        val_band = _ex.valuation_band(con, symbol)
        con.close()
        return render_template(
            "stock.html", pead=pead, risk=risk, fin_trend=fin_trend, val_band=val_band,
            d=d, symbol=symbol, our=our,
            monthly=_monthly_vm(d.get("monthly")) if d else None,
            sym=symbol, sym_name=(d["name"] if d else us["name"]),
            sym_prices=sym_prices, tv_symbol=tv_symbol, tv_embed_ok=True, candles=candles,
            back_url="/calendar?tab=earn", iv_ok=True,
        )

    kr = con.execute(
        "SELECT name, sector_code, sector_name FROM sector_map WHERE stock_code=? AND market='KR'",
        (symbol,),
    ).fetchone()
    if kr:
        d = stock_info.get_detail_kr(con, symbol, kr["sector_code"], force=force)
        our = _our_metrics(con, "kr_stock", symbol)
        sym_prices = queries.ohlcv(con, symbol)
        candles = _candles(con, symbol)
        from src.dashboard import stock_extras as _ex

        filings = _ex.dart_filings(con, symbol)
        risk = _ex.risk_profile(con, symbol)
        ins_kr = _ex.insider_kr_note(con, symbol)
        fin_trend = _ex.financials_trend(symbol, kr=True)
        val_band = _ex.valuation_band(con, symbol, kr=True)
        con.close()
        return render_template(
            "stock_kr.html", filings=filings, risk=risk, ins_kr=ins_kr,
            fin_trend=fin_trend, val_band=val_band,
            d=d, symbol=symbol, name=kr["name"], sector_name=kr["sector_name"], our=our,
            monthly=_monthly_vm(d.get("monthly")) if d else None,
            sym=symbol, sym_name=kr["name"], sym_prices=sym_prices, candles=candles,
            tv_symbol=f"KRX:{symbol}", tv_embed_ok=False, back_url="/kr-leaders",
            iv_ok=True,
        )

    con.close()
    return render_template("stock.html", d=None, symbol=symbol)
