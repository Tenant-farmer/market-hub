"""Flask 대시보드 앱 팩토리 — 페이지별 블루프린트 등록."""
from flask import Flask


def create_app() -> Flask:
    app = Flask(__name__)
    from src.dashboard.fmt import fmt_krw, fmt_usd
    from src.dashboard.pages import (
        calendar, fed, gurus, health, kr, kr_leaders, leaders, overview, stock, us,
    )

    app.jinja_env.filters["usd"] = fmt_usd
    app.jinja_env.filters["krw"] = fmt_krw
    for mod in (overview, us, kr, leaders, kr_leaders, gurus, calendar, fed, stock, health):
        app.register_blueprint(mod.bp)
    return app
