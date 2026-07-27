"""수집기 상태 페이지 (자동매매 포지션·게이트는 /positions로 분리)."""
from flask import Blueprint, render_template

from src import db

bp = Blueprint("health", __name__)


@bp.get("/health")
def health():
    con = db.connect()
    runs = con.execute("SELECT * FROM collector_runs ORDER BY id DESC LIMIT 30").fetchall()
    from src.errlog import recent          # 삼킨 예외 = 조용한 실패 (2026-07-27 실사고 대응)

    swallowed = recent(con, hours=48)
    con.close()
    return render_template("health.html", runs=[dict(r) for r in runs], swallowed=swallowed)
