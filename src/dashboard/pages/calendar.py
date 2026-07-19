"""일정 페이지 — 경제지표 / 실적 탭."""
from flask import Blueprint, render_template, request

from src import db
from src.dashboard import queries

bp = Blueprint("calendar", __name__)


@bp.get("/calendar")
def calendar_page():
    tab = request.args.get("tab", "econ")
    if tab not in ("econ", "earn"):
        tab = "econ"
    major_only = request.args.get("major") == "1"
    con = db.connect()
    econ = queries.econ_upcoming(con, days=14, limit=80)
    if major_only:
        econ = [e for e in econ if e["major"]]
    earnings = queries.earnings_upcoming(con, days=14, limit=80)
    fw = queries.fed_watch(con)
    con.close()
    return render_template(
        "calendar.html",
        tab=tab, major_only=major_only, econ=econ, earnings=earnings,
        fed_next=fw["next"] if fw else None,
    )
