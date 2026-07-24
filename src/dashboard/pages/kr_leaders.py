"""KR 주도주 페이지 — 업종명(코스피/코스닥 통합) 필터 + 시장 토글."""
from flask import Blueprint, render_template, request

from src import config, db
from src.dashboard import queries

bp = Blueprint("kr_leaders", __name__)


@bp.get("/kr-leaders")
def kr_leaders_page():
    cfg = config.load()["kr"]
    sector = request.args.get("sector", "")
    market = request.args.get("market", "")
    if market not in ("", "kp", "kq"):
        market = ""
    sort = request.args.get("sort", "score")
    if sort not in ("score", "rs21", "score63", "mcap", "vol"):
        sort = "score"
    con = db.connect()
    names = queries.kr_index_names(con)
    date_row = con.execute(
        "SELECT MAX(date) d FROM analytics_daily WHERE scope='kr_stock'"
    ).fetchone()
    rows = queries.kr_leaders(con, sector=sector, market=market, sort=sort)
    strength = queries.kr_sector_strength(con)
    top_sectors = [
        r["code"] for r in con.execute(
            "SELECT code FROM analytics_daily WHERE scope='kr_sector' AND metric='leader_score' "
            "ORDER BY value DESC LIMIT 3"
        )
    ]

    # 종목 클릭 → 차트
    sym = request.args.get("sym", "")
    sym_name, sym_prices, tv_symbol = "", [], ""
    if sym:
        nrow = con.execute(
            "SELECT name FROM sector_map WHERE stock_code=? AND market='KR'", (sym,)
        ).fetchone()
        if nrow:
            sym_name = nrow["name"]
            sym_prices = queries.ohlcv(con, sym)
            tv_symbol = f"KRX:{sym}"
        else:
            sym = ""
    con.close()

    qs = []
    if market:
        qs.append(f"market={market}")
    if sector:
        qs.append(f"sector={sector}")
    back_url = "/kr-leaders" + ("?" + "&".join(qs) if qs else "")

    def _surl(s):
        parts = [f"sort={s}"] + [p for p in (f"market={market}" if market else "",
                                             f"sector={sector}" if sector else "") if p]
        return "/kr-leaders?" + "&".join(parts)
    sort_pills = [("복합점수순", _surl("score"), sort == "score"),
                  ("순수1개월순", _surl("rs21"), sort == "rs21"),
                  ("순수3개월순", _surl("score63"), sort == "score63"),
                  ("시가총액순", _surl("mcap"), sort == "mcap"),
                  ("거래량순", _surl("vol"), sort == "vol")]
    return render_template(
        "kr_leaders.html",
        date=date_row["d"], rows=rows, sector=sector, market=market,
        sort=sort, sort_pills=sort_pills,
        strength=strength, top_sectors=top_sectors, names=names,
        min_mcap_label=f"{cfg['leader_min_mcap'] / 1e8:,.0f}억",
        sym=sym, sym_name=sym_name, sym_prices=sym_prices, tv_symbol=tv_symbol,
        tv_embed_ok=False,  # KRX는 거래소 라이선스 제한으로 임베드 위젯 표시 불가
        back_url=back_url,
    )
