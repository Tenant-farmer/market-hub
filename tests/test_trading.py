"""자동매매 배관 테스트 — 웹훅 수신기(시크릿·멱등) + 엔진(paper_log·리스크)."""
import json
import sqlite3

import pytest

from src import db as db_mod
from src.trading import engine, ensure_tables, risk, state


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
    paper = {"mode": "paper", "armed": 0}
    assert engine._pick_broker("005930", paper)[0].name == "paper_log"   # KR은 키움 전까지 기록만
    assert engine._pick_broker("AAPL", paper)[0].name == "alpaca"
    assert engine._pick_broker("BTCUSD", paper)[0].name == "alpaca"
    monkeypatch.setattr(alpaca, "configured", lambda: False)
    assert engine._pick_broker("AAPL", paper)[0].name == "paper_log"     # 키 없으면 폴백


def test_gate_modes(monkeypatch):
    from src.trading.brokers import alpaca

    monkeypatch.setattr(alpaca, "configured", lambda: True)
    # log 모드 → 무조건 paper_log
    assert engine._pick_broker("AAPL", {"mode": "log", "armed": 0})[0].name == "paper_log"
    # live + 미무장 → paper_log (안전 게이트)
    b, note = engine._pick_broker("AAPL", {"mode": "live", "armed": 0})
    assert b.name == "paper_log" and "미무장" in note
    # live + 무장 → alpaca (단 실계좌 미구현이라 페이퍼 표기)
    b, note = engine._pick_broker("AAPL", {"mode": "live", "armed": 1})
    assert b.name == "alpaca" and "페이퍼" in note


def test_gate_state_toggle(con):
    assert state.get_state(con) == {"mode": "paper", "armed": 0}   # 기본 안전
    state.set_mode(con, "live")
    state.set_armed(con, True)
    assert state.get_state(con) == {"mode": "live", "armed": 1}


def _sig(con, ticker, action="buy", qty=1, price=None):
    con.execute(
        "INSERT INTO signals (hash, received_at, ticker, action, qty, price, status) "
        "VALUES (?,?,?,?,?,?, 'new')",
        (f"{ticker}{action}{qty}{price}", "t", ticker, action, qty, price),
    )
    con.commit()
    return con.execute("SELECT * FROM signals ORDER BY id DESC LIMIT 1").fetchone()


def test_risk_max_notional(con, monkeypatch):
    monkeypatch.setenv("MAX_ORDER_USD", "1000")
    ok, reason = risk.check(con, _sig(con, "AAPL", qty=100, price=50))   # 5000 > 1000
    assert not ok and "주문금액" in reason
    ok, _ = risk.check(con, _sig(con, "AAPL", qty=10, price=50))          # 500 < 1000
    assert ok


def test_risk_daily_order_cap(con, monkeypatch):
    from datetime import date

    monkeypatch.setenv("MAX_DAILY_ORDERS", "2")
    today = date.today().isoformat()
    for i in range(2):
        con.execute(
            "INSERT INTO orders (client_order_id, broker, ticker, status, created_at) "
            "VALUES (?,?,?,?,?)", (f"o{i}", "paper_log", "AAPL", "logged", today + "T10:00:00"),
        )
    con.commit()
    ok, reason = risk.check(con, _sig(con, "AAPL"))
    assert not ok and "일일 주문 상한" in reason


def test_engine_kill_switch(client, con, monkeypatch):
    _post(client, {"secret": "test-secret", "ticker": "AAPL", "action": "buy"})
    monkeypatch.setenv("KILL_SWITCH", "1")
    res = engine.process_once(con)
    assert res == {"processed": 0, "rejected": 1}
    assert con.execute("SELECT COUNT(*) n FROM orders").fetchone()["n"] == 0
