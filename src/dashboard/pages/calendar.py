"""일정 페이지 — 기업실적 / 경제지표를 **별도 라우트**로 분리.

원래 `/calendar?tab=earn|econ` 하나에 붙어 있었는데 성격이 다른 두 화면이라 분리했다
(2026-07-29 사용자 요청). Fed Watch는 이미 `/fed`로 독립돼 있다.
`/calendar`는 옛 링크·북마크를 위해 `/earnings`로 넘긴다.
"""
from flask import Blueprint, redirect, render_template, request

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
def calendar_legacy():
    """구 경로 — tab에 따라 새 라우트로 넘긴다."""
    return redirect("/econ" if request.args.get("tab") == "econ" else "/earnings", code=301)


@bp.get("/earnings")
def earnings_page():
    view = request.args.get("view", "week")
    if view not in ("list", "week", "month"):
        view = "week"
    con = db.connect()
    earnings = grid = None
    if view == "list":
        earnings = queries.earnings_upcoming(con, days=45, limit=120)
    elif view == "week":
        grid = queries.earnings_week(con, _int_arg("w", -4, 6))
    else:
        grid = queries.earnings_month(con, _int_arg("m", -1, 1))
    fw = queries.fed_watch(con)
    con.close()
    return render_template("earnings.html", view=view, earnings=earnings, grid=grid,
                           fed_next=fw["next"] if fw else None)


@bp.get("/econ")
def econ_page():
    major_only = request.args.get("major") == "1"
    con = db.connect()
    econ = queries.econ_upcoming(con, days=14, limit=80)
    if major_only:
        econ = [e for e in econ if e["major"]]
    fw = queries.fed_watch(con)
    con.close()
    return render_template("econ.html", econ=econ, major_only=major_only,
                           fed_next=fw["next"] if fw else None)
