"""Flask 대시보드 앱 팩토리 — 페이지별 블루프린트 등록."""
from flask import Flask


def create_app() -> Flask:
    app = Flask(__name__)
    from src.dashboard.pages import gurus, health, kr, leaders, us

    for mod in (us, kr, leaders, gurus, health):
        app.register_blueprint(mod.bp)
    return app
