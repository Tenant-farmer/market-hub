"""KR 섹터 로테이션 페이지 — US 섹터와 동일 구성 (수급은 개요, 주도주는 /kr-leaders)."""
from flask import Blueprint, render_template, request

from src import config, db
from src.dashboard import queries
from src.dashboard.fmt import QUAD, QUAD_KO

bp = Blueprint("kr", __name__)


@bp.get("/kr")
def kr():
    cfg = config.load()["kr"]
    con = db.connect()
    names = queries.kr_index_names(con)
    date, ranking = queries.ranking(con, "kr_sector")
    trails = queries.trails(con, "kr_sector")
    symbols = [cfg["benchmark"]] + cfg["sector_codes"]
    sym = request.args.get("sym", "1013")   # 기본: 전기전자
    if sym not in symbols:
        sym = "1013"
    prices = queries.prices(con, sym)
    rel = queries.rel_ratio_series(con, list(trails.keys()), cfg["benchmark"])
    cards = queries.leader_cards(ranking, names)
    radar = queries.theme_radar(con)
    con.close()
    return render_template(
        "kr.html",
        date=date, ranking=ranking, trails=trails, prices=prices, sym=sym,
        rel=rel, bench=cfg["benchmark"],
        names=names, symbols=symbols, quad=QUAD, quad_ko=QUAD_KO, cards=cards,
        price_label="최근 1년 (지수)", radar=radar,
    )
