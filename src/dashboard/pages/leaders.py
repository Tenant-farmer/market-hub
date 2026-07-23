"""주도주(S&P500 종목) 페이지."""
from flask import Blueprint, render_template, request

from src import config, db
from src.dashboard import queries
from src.dashboard.fmt import fmt_usd

bp = Blueprint("leaders", __name__)


@bp.get("/leaders")
def leaders_page():
    cfg = config.load()["us"]
    names = cfg.get("names", {})
    sector = request.args.get("sector", "")
    con = db.connect()

    # 티커 클릭 → 차트
    sym = request.args.get("sym", "")
    sym_name, sym_prices, tv_symbol = "", [], ""
    if sym:
        nrow = con.execute(
            "SELECT name FROM sector_map WHERE stock_code=? AND market='US_STOCK'", (sym,)
        ).fetchone()
        if nrow:
            sym_name = nrow["name"]
            sym_prices = queries.ohlcv(con, sym)
            tvrow = con.execute(
                "SELECT tv_symbol FROM stock_meta WHERE symbol=?", (sym,)
            ).fetchone()
            tv_symbol = (tvrow["tv_symbol"] if tvrow and tvrow["tv_symbol"] else sym)
        else:
            sym = ""

    date_row = con.execute(
        "SELECT MAX(date) d FROM analytics_daily WHERE scope='us_stock'"
    ).fetchone()
    date = date_row["d"]

    where = "a.scope='us_stock' AND a.date=?"
    params: list = [date]
    if sector:
        where += " AND m.sector_code=?"
        params.append(sector)
    rows = con.execute(
        f"""
        SELECT a.code, m.name, m.sector_code, sm.mcap,
               MAX(CASE WHEN a.metric='leader_score' THEN a.value END) score,
               MAX(CASE WHEN a.metric='ret_21' THEN a.value END)      ret21,
               MAX(CASE WHEN a.metric='rs_mkt_21' THEN a.value END)   rs_mkt,
               MAX(CASE WHEN a.metric='rs_mkt_63' THEN a.value END)   rs_mkt63,
               MAX(CASE WHEN a.metric='rs_sec_21' THEN a.value END)   rs_sec,
               MAX(CASE WHEN a.metric='vol_surge' THEN a.value END)   vol_surge,
               MAX(CASE WHEN a.metric='high_prox' THEN a.value END)   high_prox
        FROM analytics_daily a
        JOIN sector_map m ON m.stock_code = a.code
        LEFT JOIN stock_meta sm ON sm.symbol = a.code
        WHERE {where}
        GROUP BY a.code ORDER BY score DESC LIMIT 50
        """,
        params,
    ).fetchall()
    rows = [dict(r) | {"mcap_fmt": fmt_usd(r["mcap"]) if r["mcap"] else None} for r in rows]

    sectors = [
        r["sector_code"] for r in con.execute(
            "SELECT DISTINCT sector_code FROM sector_map WHERE market='US_STOCK' ORDER BY sector_code"
        )
    ]
    top_sectors = [
        r["code"] for r in con.execute(
            "SELECT code FROM analytics_daily WHERE scope='us_sector' AND metric='leader_score' "
            "ORDER BY value DESC LIMIT 3"
        )
    ]
    con.close()
    return render_template(
        "leaders.html",
        date=date, rows=rows, sector=sector,
        sectors=sectors, top_sectors=top_sectors, names=names,
        sym=sym, sym_name=sym_name, sym_prices=sym_prices, tv_symbol=tv_symbol,
        tv_embed_ok=True,   # 미국 심볼은 위젯 재배포 허용
        back_url=f"/leaders?sector={sector}" if sector else "/leaders",
    )
