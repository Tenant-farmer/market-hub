"""US 섹터 로테이션 페이지 (+ 홈)."""
from flask import Blueprint, render_template, request

from src import config, db
from src.dashboard import queries
from src.dashboard.fmt import QUAD, QUAD_KO

bp = Blueprint("us", __name__)


@bp.get("/us")
def us():
    cfg = config.load()["us"]
    con = db.connect()
    date, ranking = queries.ranking(con, "us_sector")
    trails = queries.trails(con, "us_sector")
    sym = request.args.get("sym", "SMH")
    if sym not in cfg["symbols"]:
        sym = "SMH"
    prices = queries.prices(con, sym)
    con.close()
    return render_template(
        "us.html",
        date=date, ranking=ranking, trails=trails, prices=prices, sym=sym,
        names=cfg.get("names", {}), symbols=cfg["symbols"],
        quad=QUAD, quad_ko=QUAD_KO,
        cards=queries.leader_cards(ranking, cfg.get("names", {})),
        price_label="최근 1년 (수정종가)",
    )
