"""Flask 대시보드 앱 팩토리 — 페이지별 블루프린트 등록."""
from flask import Flask


def create_app() -> Flask:
    app = Flask(__name__)
    from src.dashboard.fmt import fmt_krw, fmt_usd
    from src.dashboard.pages import (
        calendar, fed, gurus, health, kr, kr_leaders, leaders, overview, positions, signals,
        stock, stocks, us,
    )

    app.jinja_env.filters["usd"] = fmt_usd
    app.jinja_env.filters["krw"] = fmt_krw
    for mod in (overview, us, kr, leaders, kr_leaders, stocks, signals, gurus, calendar, fed,
                stock, positions, health):
        app.register_blueprint(mod.bp)

    from src.trading import receiver   # 웹훅 수신기 (POST /hook/tv)

    app.register_blueprint(receiver.bp)

    from src.dashboard.auth import require_auth   # Basic Auth (DASH_PASS 설정 시)

    app.before_request(require_auth)

    import gzip

    from flask import request

    @app.after_request
    def _gzip(resp):
        """텍스트 응답 gzip — 지표분석 등 시계열 JSON이 1MB+라 터널 공유 시 체감 좌우."""
        if (resp.direct_passthrough or resp.status_code != 200
                or "gzip" not in (request.headers.get("Accept-Encoding") or "")
                or resp.content_length is None or resp.content_length < 500
                or not (resp.mimetype or "").startswith(("text/", "application/json",
                                                         "application/javascript"))):
            return resp
        resp.set_data(gzip.compress(resp.get_data(), compresslevel=6))
        resp.headers["Content-Encoding"] = "gzip"
        resp.headers["Content-Length"] = str(len(resp.get_data()))
        resp.headers["Vary"] = "Accept-Encoding"
        return resp

    return app
