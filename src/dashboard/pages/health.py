"""수집기 상태 페이지."""
from flask import Blueprint, render_template

from src import db

bp = Blueprint("health", __name__)


@bp.get("/health")
def health():
    con = db.connect()
    runs = con.execute("SELECT * FROM collector_runs ORDER BY id DESC LIMIT 30").fetchall()
    con.close()
    return render_template("health.html", runs=[dict(r) for r in runs])
