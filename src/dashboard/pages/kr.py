"""KR 섹터 로테이션 + 수급 + 주도주 페이지."""
from flask import Blueprint, render_template, request

from src import config, db
from src.dashboard import queries
from src.dashboard.fmt import QUAD, QUAD_KO, fmt_krw

bp = Blueprint("kr", __name__)

INV_KO = {"foreign": "외국인", "institution": "기관", "individual": "개인"}


def _market_flows(con):
    """시장 단위 수급: (시장, 투자자)별 최근 5일/20일 누적."""
    rows = con.execute(
        "SELECT code, date, investor, net_value FROM investor_flows "
        "WHERE scope='market' ORDER BY date DESC"
    ).fetchall()
    series: dict[tuple, list[float]] = {}
    for r in rows:
        series.setdefault((r["code"], r["investor"]), []).append(r["net_value"])
    out = []
    for mkt in ("KOSPI", "KOSDAQ"):
        for inv in ("foreign", "institution", "individual"):
            vals = series.get((mkt, inv), [])
            if not vals:
                continue
            d5, d20 = sum(vals[:5]), sum(vals[:20])
            out.append({
                "mkt": mkt, "inv_ko": INV_KO[inv],
                "d5": d5, "d20": d20,
                "d5_fmt": fmt_krw(d5), "d20_fmt": fmt_krw(d20),
            })
    return out


def _top_flow_stocks(con, investor: str, n: int = 10):
    # sector_map은 PK(stock_code)로 종목당 1행 — KR(업종 매핑) 또는 KR_NAME(이름 캐시)
    rows = con.execute(
        """
        SELECT f.code, f.net_value, m.name
        FROM investor_flows f
        LEFT JOIN sector_map m ON m.stock_code = f.code
        WHERE f.scope='stock' AND f.investor=?
        ORDER BY f.net_value DESC LIMIT ?
        """,
        (investor, n),
    ).fetchall()
    return [{"name": r["name"] or r["code"], "amt": fmt_krw(r["net_value"])} for r in rows]


def _kr_leaders(con, n: int = 20):
    date_row = con.execute(
        "SELECT MAX(date) d FROM analytics_daily WHERE scope='kr_stock'"
    ).fetchone()
    if date_row["d"] is None:
        return []
    min_mcap = config.load()["kr"]["leader_min_mcap"]
    rows = con.execute(
        """
        SELECT a.code, m.name, m.sector_name, sm.mcap,
               MAX(CASE WHEN a.metric='leader_score' THEN a.value END) score,
               MAX(CASE WHEN a.metric='rs_mkt_21' THEN a.value END)   rs_mkt,
               MAX(CASE WHEN a.metric='rs_sec_21' THEN a.value END)   rs_sec,
               MAX(CASE WHEN a.metric='vol_surge' THEN a.value END)   vol_surge,
               MAX(CASE WHEN a.metric='high_prox' THEN a.value END)   high_prox
        FROM analytics_daily a
        JOIN sector_map m ON m.stock_code = a.code AND m.market = 'KR'
        JOIN stock_meta sm ON sm.symbol = a.code AND sm.mcap >= ?
        WHERE a.scope='kr_stock' AND a.date=?
        GROUP BY a.code ORDER BY score DESC LIMIT ?
        """,
        (min_mcap, date_row["d"], n),
    ).fetchall()
    return [dict(r) | {"mcap_fmt": fmt_krw(r["mcap"]) if r["mcap"] else None} for r in rows]


@bp.get("/kr")
def kr():
    cfg = config.load()["kr"]
    con = db.connect()
    names = {
        r["stock_code"]: r["name"]
        for r in con.execute("SELECT stock_code, name FROM sector_map WHERE market='KR_INDEX'")
    }
    date, ranking = queries.ranking(con, "kr_sector")
    trails = queries.trails(con, "kr_sector")
    symbols = [cfg["benchmark"]] + cfg["sector_codes"]
    sym = request.args.get("sym", "1013")   # 기본: 전기전자
    if sym not in symbols:
        sym = "1013"
    prices = queries.prices(con, sym)
    cards = queries.leader_cards(ranking, names)
    mflows = _market_flows(con)
    top_foreign = _top_flow_stocks(con, "foreign")
    top_inst = _top_flow_stocks(con, "institution")
    krl = _kr_leaders(con)
    con.close()
    return render_template(
        "kr.html",
        date=date, ranking=ranking, trails=trails, prices=prices, sym=sym,
        names=names, symbols=symbols, quad=QUAD, quad_ko=QUAD_KO, cards=cards,
        price_label="최근 1년 (지수)",
        mflows=mflows, top_foreign=top_foreign, top_inst=top_inst,
        top_n=10, krl=krl,
    )
