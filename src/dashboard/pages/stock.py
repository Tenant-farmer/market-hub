"""종목 상세 페이지 (US) — yfinance 온디맨드 + 자체 분석 결합."""
from flask import Blueprint, render_template, request

from src import db, stock_info
from src.dashboard import queries

bp = Blueprint("stock", __name__)


def _heat(v):
    if v is None:
        return ""
    a = min(abs(v) / 10, 1) * 0.45
    return (f"rgba(12,163,12,{a:.2f})" if v > 0 else f"rgba(208,59,59,{a:.2f})")


@bp.get("/stock/<symbol>")
def stock_page(symbol):
    symbol = symbol.upper()
    con = db.connect()
    known = con.execute(
        "SELECT name FROM sector_map WHERE stock_code=? AND market='US_STOCK'", (symbol,)
    ).fetchone()
    if not known:
        con.close()
        return render_template("stock.html", d=None, symbol=symbol)

    d = stock_info.get_detail(con, symbol, force=request.args.get("refresh") == "1")

    # 자체 분석 (주도점수 등)
    our = None
    rows = con.execute(
        """
        SELECT metric, value FROM analytics_daily
        WHERE scope='us_stock' AND code=?
          AND date=(SELECT MAX(date) FROM analytics_daily WHERE scope='us_stock')
        """,
        (symbol,),
    ).fetchall()
    if rows:
        our = {r["metric"]: r["value"] for r in rows}

    tvrow = con.execute("SELECT tv_symbol FROM stock_meta WHERE symbol=?", (symbol,)).fetchone()
    tv_symbol = tvrow["tv_symbol"] if tvrow and tvrow["tv_symbol"] else symbol
    sym_prices = queries.ohlcv(con, symbol)
    con.close()

    monthly = None
    if d and d.get("monthly"):
        monthly = {
            "avg": [{"v": v, "bg": _heat(v)} for v in d["monthly"]["avg"]],
            "years": [
                {"y": yr["y"], "m": [{"v": v, "bg": _heat(v)} for v in yr["m"]]}
                for yr in d["monthly"]["years"]
            ],
            "cur": d["monthly"]["cur"],
        }

    return render_template(
        "stock.html",
        d=d, symbol=symbol, our=our, monthly=monthly,
        sym=symbol, sym_name=(d["name"] if d else known["name"]),
        sym_prices=sym_prices, tv_symbol=tv_symbol, tv_embed_ok=True,
        back_url="/calendar?tab=earn",
    )
