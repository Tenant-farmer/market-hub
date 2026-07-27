"""일정 페이지 — 경제지표 / 실적 탭. 실적은 목록·주간격자·월간격자 3뷰."""
from flask import Blueprint, render_template, request

from src import db
from src.dashboard import queries

bp = Blueprint("calendar", __name__)


def _int_arg(name, lo, hi):
    """뷰 이동 오프셋 — 범위를 넘으면 데이터가 없으므로 클램프."""
    try:
        return max(lo, min(hi, int(request.args.get(name, 0))))
    except (TypeError, ValueError):
        return 0


@bp.get("/calendar")
def calendar_page():
    tab = request.args.get("tab", "econ")
    if tab not in ("econ", "earn"):
        tab = "econ"
    view = request.args.get("view", "week")
    if view not in ("list", "week", "month"):
        view = "week"
    major_only = request.args.get("major") == "1"
    con = db.connect()
    econ = queries.econ_upcoming(con, days=14, limit=80)
    if major_only:
        econ = [e for e in econ if e["major"]]
    earnings = grid = None
    if view == "list":
        earnings = queries.earnings_upcoming(con, days=14, limit=80)
    elif view == "week":
        grid = queries.earnings_week(con, _int_arg("w", -4, 6))
    else:
        grid = queries.earnings_month(con, _int_arg("m", -1, 1))
    fw = queries.fed_watch(con)
    con.close()
    return render_template(
        "calendar.html",
        tab=tab, view=view, major_only=major_only, econ=econ, earnings=earnings, grid=grid,
        fed_next=fw["next"] if fw else None,
    )
