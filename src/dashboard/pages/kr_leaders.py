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
        sectors_kp=cfg["sector_codes"], sectors_kq=cfg.get("kosdaq_sector_codes", []),
        top_sectors=top_sectors, names=names,
        min_mcap_label=f"{cfg['leader_min_mcap'] / 1e8:,.0f}억",
        sym=sym, sym_name=sym_name, sym_prices=sym_prices, tv_symbol=tv_symbol,
        tv_embed_ok=False,  # KRX는 거래소 라이선스 제한으로 임베드 위젯 표시 불가
        back_url=f"/kr-leaders?sector={sector}" if sector else "/kr-leaders",
    )
