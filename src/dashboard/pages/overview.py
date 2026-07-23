"""종합 개요 — KR+US 큰그림 한 화면."""
from flask import Blueprint, render_template

from src import config, db
from src.dashboard import queries
from src.dashboard.fmt import fng_label

bp = Blueprint("overview", __name__)


def _spark(con, sym: str, n: int = 30):
    """최근 n거래일 종가 → SVG polyline 포인트 (viewBox 0 0 100 32)."""
    px = queries.prices(con, sym, n)
    if len(px) < 2:
        return None
    vals = [p["value"] for p in px]
    lo, hi = min(vals), max(vals)
    rng = (hi - lo) or 1
    pts = " ".join(
        f"{i / (len(vals) - 1) * 100:.1f},{2 + (1 - (v - lo) / rng) * 28:.1f}"
        for i, v in enumerate(vals)
    )
    return {"pts": pts, "up": vals[-1] >= vals[0]}


@bp.get("/")
def home():
    cfg = config.load()
    us_cfg = cfg["us"]
    us_bench = us_cfg["benchmark"]
    kr_bench = cfg["kr"]["benchmark"]
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
    sparks = {s: _spark(con, s) for s in ("SPY", "QQQ", "1001", "2001")}
    macro = queries.macro_context(con)
    signal = queries.vix_signal(con)
    kq_ratio = queries.market_ratio(con)
    earnings = queries.earnings_upcoming(con)
    econ = queries.econ_upcoming(con)
    trend = queries.investor_trend(con)
    treasury = queries.treasury_line(con)
    fw = queries.fed_watch(con)
    fed_next = fw["next"] if fw else None
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

    # 업종 상대수익 겹침 차트 (KR: 수급 테이블 아래 / US: 쏠림·CapEx 행 아래)
    kr_rel = queries.rel_ratio_series(con, [r["code"] for r in kr_ranking], kr_bench)
    us_rel = queries.rel_ratio_series(con, [r["code"] for r in us_ranking], us_bench)

    # 자금 쏠림 (거래대금 점유율, ranking 재사용) + 섹터 CapEx
    us_flow = sorted(
        (r for r in us_ranking if r.get("vshare") is not None),
        key=lambda r: -r["vshare"],
    )
    capex = queries.us_capex(con)
    kr_vs = sorted(
        (r for r in kr_ranking if r.get("vshare") is not None),
        key=lambda r: -r["vshare"],
    )
    kr_flow = kr_vs[:9] + kr_vs[-5:] if len(kr_vs) > 14 else kr_vs   # 쏠림 상위 9 + 이탈 하위 5
    kr_capex = queries.kr_capex(con)

    # KR 수급
    mflows = queries.market_flows(con)
    top_foreign = queries.top_flow_stocks(con, "foreign", 5)
    top_inst = queries.top_flow_stocks(con, "institution", 5)
    sflows = queries.sector_flows(con, kr_names)
    sflows_in, sflows_out = sflows[:5], [s for s in reversed(sflows[-5:]) if s["tot_1w"] < 0]

    # 뉴스 (Google RSS + yfinance news, 매시 수집)
    try:
        news = [dict(r) for r in con.execute(
            "SELECT dt, market, title, url, source, keyword FROM news "
            "WHERE source != 'DART' ORDER BY dt DESC LIMIT 6").fetchall()]
        # 공시 보장 슬롯 (공시 dt는 09:00 고정이라 뉴스에 밀림 — 최근 2건 별도 확보)
        news += [dict(r) for r in con.execute(
            "SELECT dt, market, title, url, source, keyword FROM news "
            "WHERE source='DART' AND dt >= datetime('now','localtime','-2 days') "
            "ORDER BY dt DESC LIMIT 2").fetchall()]
    except Exception:                     # 첫 수집 전엔 테이블 없음
        news = []

    fresh = queries.freshness(con)
    con.close()
    return render_template(
        "overview.html",
        spy=spy, kospi=kospi,
        qqq=qqq, kosdaq=kosdaq, qqq_regime=qqq_regime, kosdaq_regime=kosdaq_regime,
        spy_regime=spy_regime, kospi_regime=kospi_regime, macro=macro, signal=signal,
        sparks=sparks,
        kq_ratio=kq_ratio, earnings=earnings, econ=econ, trend=trend, treasury=treasury,
        fed_next=fed_next,
        fng=fng, fng_label=fng_label(fng["value"]) if fng else None, vix=vix,
        fng_angle=fng_angle, fng_color=fng_color,
        us_date=us_date, kr_date=kr_date,
        us_cards=us_cards, kr_cards=kr_cards,
        hot_us=hot_us, hot_kr=hot_kr,
        mflows=mflows, top_foreign=top_foreign, top_inst=top_inst,
        sflows_in=sflows_in, sflows_out=sflows_out,
        kr_rel=kr_rel, kr_names=kr_names, kr_bench=kr_bench,
        us_rel=us_rel, us_names=us_names, us_bench=us_bench,
        us_flow=us_flow, capex=capex,
        kr_flow=kr_flow, kr_capex=kr_capex,
        news=news, fresh=fresh,
    )
