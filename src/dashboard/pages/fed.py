"""Fed Watch 페이지 — FOMC 금리 결정 추적."""
from flask import Blueprint, render_template

from src import db
from src.dashboard import queries

bp = Blueprint("fed", __name__)


@bp.get("/fed")
def fed_page():
    con = db.connect()
    fw = queries.fed_watch(con)
    con.close()
    return render_template("fed.html", fw=fw)
