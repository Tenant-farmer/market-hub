"""자동매매 배관 테스트 — 웹훅 수신기(시크릿·멱등) + 엔진(paper_log·리스크)."""
import json
import sqlite3

import pytest

from src import db as db_mod
from src.trading import engine, ensure_tables


@pytest.fixture
def con():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    ensure_tables(c)
    yield c
    c.close()


class _NoClose:
    """close()를 무시하는 커넥션 프록시 — 수신기가 닫아도 픽스처 커넥션 유지."""

    def __init__(self, inner):
        self._inner = inner

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def close(self):
        pass


@pytest.fixture
def client(monkeypatch, con):
    monkeypatch.setenv("WEBHOOK_SECRET", "test-secret")
    monkeypatch.setattr(db_mod, "connect", lambda: _NoClose(con))
    from src.dashboard import create_app

    app = create_app()
    app.testing = True
    return app.test_client()


def _post(client, body):
    return client.post("/hook/tv", data=json.dumps(body), content_type="application/json")


def test_hook_rejects_wrong_secret(client):
    r = _post(client, {"secret": "wrong", "ticker": "AAPL", "action": "buy"})
    assert r.status_code == 403


def test_hook_rejects_bad_payload(client):
    r = _post(client, {"secret": "test-secret", "ticker": "", "action": "buy"})
    assert r.status_code == 400
    r = _post(client, {"secret": "test-secret", "ticker": "AAPL", "action": "hold"})
    assert r.status_code == 400


def test_hook_inserts_and_dedupes(client, con):
    body = {"secret": "test-secret", "ticker": "AAPL", "action": "buy", "qty": 2, "time": "t1"}
    r1 = _post(client, body)
    assert r1.status_code == 200 and r1.get_json()["dup"] is False
    r2 = _post(client, body)   # TV 재전송 시뮬레이션
    assert r2.status_code == 200 and r2.get_json()["dup"] is True
    rows = con.execute("SELECT * FROM signals").fetchall()
    assert len(rows) == 1
    assert rows[0]["ticker"] == "AAPL" and rows[0]["qty"] == 2.0
    # 다른 시점(time) 알림은 별개 신호로 통과
    body["time"] = "t2"
    assert _post(client, body).get_json()["dup"] is False
    assert len(con.execute("SELECT * FROM signals").fetchall()) == 2


def test_engine_paper_log_and_risk(client, con):
    _post(client, {"secret": "test-secret", "ticker": "005930", "action": "buy", "qty": 10})
    _post(client, {"secret": "test-secret", "ticker": "TSLA", "action": "sell", "qty": 999999})
    res = engine.process_once(con)
    assert res == {"processed": 1, "rejected": 1}   # 팻핑거(qty 초과)는 리스크 게이트에서 거부
    orders = con.execute("SELECT * FROM orders").fetchall()
    assert len(orders) == 1
    assert orders[0]["broker"] == "paper_log" and orders[0]["status"] == "logged"
    assert orders[0]["ticker"] == "005930"
    sig = con.execute("SELECT status, result FROM signals WHERE ticker='TSLA'").fetchone()
    assert sig["status"] == "rejected" and "수량" in sig["result"]
    # 엔진 재실행 시 이미 처리된 신호는 건드리지 않음
    assert engine.process_once(con) == {"processed": 0, "rejected": 0}


def test_broker_routing(monkeypatch):
    from src.trading.brokers import alpaca

    monkeypatch.setattr(alpaca, "configured", lambda: True)
    assert engine._pick_broker("005930").name == "paper_log"   # KR은 키움 전까지 기록만
    assert engine._pick_broker("AAPL").name == "alpaca"
    assert engine._pick_broker("BTCUSD").name == "alpaca"
    monkeypatch.setattr(alpaca, "configured", lambda: False)
    assert engine._pick_broker("AAPL").name == "paper_log"     # 키 없으면 폴백


def test_engine_kill_switch(client, con, monkeypatch):
    _post(client, {"secret": "test-secret", "ticker": "AAPL", "action": "buy"})
    monkeypatch.setenv("KILL_SWITCH", "1")
    res = engine.process_once(con)
    assert res == {"processed": 0, "rejected": 1}
    assert con.execute("SELECT COUNT(*) n FROM orders").fetchone()["n"] == 0
