"""KR 주도주 페이지."""
from flask import Blueprint, render_template, request

from src import config, db
from src.dashboard import queries

bp = Blueprint("kr_leaders", __name__)


@bp.get("/kr-leaders")
def kr_leaders_page():
    cfg = config.load()["kr"]
    sector = request.args.get("sector", "")
    con = db.connect()
    names = queries.kr_index_names(con)
    date_row = con.execute(
        "SELECT MAX(date) d FROM analytics_daily WHERE scope='kr_stock'"
    ).fetchone()
    rows = queries.kr_leaders(con, sector=sector)
    top_sectors = [
        r["code"] for r in con.execute(
            "SELECT code FROM analytics_daily WHERE scope='kr_sector' AND metric='leader_score' "
            "ORDER BY value DESC LIMIT 3"
        )
    ]
    con.close()
    return render_template(
        "kr_leaders.html",
        date=date_row["d"], rows=rows, sector=sector,
        sectors=cfg["sector_codes"], top_sectors=top_sectors, names=names,
        min_mcap_label=f"{cfg['leader_min_mcap'] / 1e8:,.0f}억",
    )
