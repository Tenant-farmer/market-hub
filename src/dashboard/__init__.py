"""Flask 대시보드 앱 팩토리 — 페이지별 블루프린트 등록."""
from flask import Flask


def create_app() -> Flask:
    app = Flask(__name__)
    from src.dashboard.fmt import fmt_krw, fmt_usd
    from src.dashboard.pages import (
        calendar, fed, gurus, health, kr, kr_leaders, leaders, overview, positions, stock,
        stocks, us,
    )

    app.jinja_env.filters["usd"] = fmt_usd
    app.jinja_env.filters["krw"] = fmt_krw
    for mod in (overview, us, kr, leaders, kr_leaders, stocks, gurus, calendar, fed, stock,
                positions, health):
        app.register_blueprint(mod.bp)

    from src.trading import receiver   # 웹훅 수신기 (POST /hook/tv)

    app.register_blueprint(receiver.bp)

    from src.dashboard.auth import require_auth   # Basic Auth (DASH_PASS 설정 시)

    app.before_request(require_auth)
    return app
