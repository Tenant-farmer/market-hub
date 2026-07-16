"""종합 개요 — KR+US 큰그림 한 화면."""
from flask import Blueprint, render_template

from src import config, db
from src.dashboard import queries
from src.dashboard.fmt import fng_label

bp = Blueprint("overview", __name__)


@bp.get("/")
def home():
    us_cfg = config.load()["us"]
    con = db.connect()

    us_names = us_cfg.get("names", {})
    kr_names = queries.kr_index_names(con)

    # 시장 온도 카드 (+ 200일선 레짐 신호등)
    spy = queries.bench_snapshot(con, "SPY")
    qqq = queries.bench_snapshot(con, "QQQ")
    kospi = queries.bench_snapshot(con, "1001")
    kosdaq = queries.bench_snapshot(con, "2001")
    spy_regime = queries.regime(con, "SPY")
    qqq_regime = queries.regime(con, "QQQ")
    kospi_regime = queries.regime(con, "1001")
    kosdaq_regime = queries.regime(con, "2001")
    macro = queries.macro_context(con)
    signal = queries.vix_signal(con)
    kq_ratio = queries.market_ratio(con)
    senti = queries.sentiment_latest(con)
    fng = senti.get("fear_greed")
    fng_angle = round(fng["value"] * 1.8 - 90, 1) if fng else None
    fng_color = None
    if fng:
        v = fng["value"]
        fng_color = ("#e66767" if v <= 25 else "#ec835a" if v < 45 else
                     "#fab219" if v <= 55 else "#199e70" if v < 75 else "#0ca30c")
    vix = senti.get("vix")

    # 주도 섹터 TOP3 (양국)
    us_date, us_ranking = queries.ranking(con, "us_sector")
    kr_date, kr_ranking = queries.ranking(con, "kr_sector")
    us_cards = queries.leader_cards(us_ranking, us_names)
    kr_cards = queries.leader_cards(kr_ranking, kr_names)

    # 과열 플래그
    hot_us = queries.overheat_list(con, "us_sector", us_names)
    hot_kr = queries.overheat_list(con, "kr_sector", kr_names)

    # KR 수급
    mflows = queries.market_flows(con)
    top_foreign = queries.top_flow_stocks(con, "foreign", 5)
    top_inst = queries.top_flow_stocks(con, "institution", 5)
    sflows = queries.sector_flows(con, kr_names)
    sflows_in, sflows_out = sflows[:5], [s for s in reversed(sflows[-5:]) if s["tot_1w"] < 0]

    fresh = queries.freshness(con)
    con.close()
    return render_template(
        "overview.html",
        spy=spy, kospi=kospi,
        qqq=qqq, kosdaq=kosdaq, qqq_regime=qqq_regime, kosdaq_regime=kosdaq_regime,
        spy_regime=spy_regime, kospi_regime=kospi_regime, macro=macro, signal=signal,
        kq_ratio=kq_ratio,
        fng=fng, fng_label=fng_label(fng["value"]) if fng else None, vix=vix,
        fng_angle=fng_angle, fng_color=fng_color,
        us_date=us_date, kr_date=kr_date,
        us_cards=us_cards, kr_cards=kr_cards,
        hot_us=hot_us, hot_kr=hot_kr,
        mflows=mflows, top_foreign=top_foreign, top_inst=top_inst,
        sflows_in=sflows_in, sflows_out=sflows_out,
        fresh=fresh,
    )
