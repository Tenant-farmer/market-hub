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


def test_hook_immediate_process(monkeypatch):
    """수신 즉시 처리 — 엔진 호출 + 동시 실행 방지 락(점유 중이면 스킵)."""
    from src.trading import engine as eng
    from src.trading import receiver

    calls = []
    monkeypatch.setattr(eng, "process_once", lambda: calls.append(1))
    receiver._process_now()
    assert calls == [1]
    receiver._proc_lock.acquire()                  # 다른 스레드가 처리 중인 상황
    try:
        receiver._process_now()                    # 락 점유 → 스킵 (큐는 그쪽이 비움)
        assert calls == [1]
    finally:
        receiver._proc_lock.release()


def test_engine_paper_log_and_risk(client, con, monkeypatch):
    from src.trading.brokers import alpaca, kiwoom

    monkeypatch.setattr(kiwoom, "configured", lambda: False)   # 라우팅 결정론적(앰비언트 env 무관)
    monkeypatch.setattr(alpaca, "configured", lambda: False)
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
    from src.trading.brokers import alpaca, kiwoom

    monkeypatch.setattr(alpaca, "configured", lambda: True)
    monkeypatch.setattr(kiwoom, "configured", lambda: False)   # 키움 미설정 시
    paper = {"mode": "paper", "armed": 0}
    assert engine._pick_broker("005930", paper)[0].name == "paper_log"   # 키움 없으면 KR은 기록만
    assert engine._pick_broker("AAPL", paper)[0].name == "alpaca"
    assert engine._pick_broker("BTCUSD", paper)[0].name == "alpaca"
    monkeypatch.setattr(alpaca, "configured", lambda: False)
    assert engine._pick_broker("AAPL", paper)[0].name == "paper_log"     # 키 없으면 폴백


def test_kiwoom_routing(monkeypatch):
    from src.trading.brokers import kiwoom

    monkeypatch.setattr(kiwoom, "configured", lambda: True)
    paper, live = {"mode": "paper", "armed": 0}, {"mode": "live", "armed": 1}

    # 모의(KIWOOM_MOCK=1): paper 모드에서 KR → kiwoom
    monkeypatch.setattr(kiwoom, "is_mock", lambda: True)
    b, note = engine._pick_broker("005930", paper)
    assert b.name == "kiwoom" and note == "kiwoom-mock"

    # 실계좌(mock=0) + paper 모드 → 안전상 paper_log (실주문 차단)
    monkeypatch.setattr(kiwoom, "is_mock", lambda: False)
    assert engine._pick_broker("005930", paper)[0].name == "paper_log"
    # 실계좌(mock=0) + armed-live → kiwoom-live
    b2, note2 = engine._pick_broker("005930", live)
    assert b2.name == "kiwoom" and note2 == "kiwoom-live"


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


def test_exit_rules(con, monkeypatch):
    from src.trading import exits

    con.execute(
        "CREATE TABLE prices_daily (symbol TEXT, market TEXT, date TEXT, open REAL, high REAL, "
        "low REAL, close REAL, volume REAL, value REAL, PRIMARY KEY(symbol, date))"
    )
    for i in range(25):                              # UP: 우상향 → 마지막 종가 > 20MA
        con.execute("INSERT INTO prices_daily (symbol, date, close) VALUES (?,?,?)",
                    ("UP", f"2026-06-{i + 1:02d}", 100 + i))
    for i in range(25):                              # DOWN: 우하향 → 마지막 종가 < 20MA
        con.execute("INSERT INTO prices_daily (symbol, date, close) VALUES (?,?,?)",
                    ("DOWN", f"2026-06-{i + 1:02d}", 100 - i))
    con.commit()
    assert "손절" in exits._eval(con, {"code": "UP", "qty": 1, "plpc": -10})     # 손절 우선
    # 추세이탈(20MA)은 기본 off (백테스트상 휩쏘로 해로움)
    assert exits._eval(con, {"code": "DOWN", "qty": 1, "plpc": -1}) is None
    monkeypatch.setenv("EXIT_MA_ENABLED", "1")                                    # 켜면 하락추세 감지
    assert "추세이탈" in exits._eval(con, {"code": "DOWN", "qty": 1, "plpc": -1})
    assert exits._eval(con, {"code": "UP", "qty": 1, "plpc": 2}) is None          # 건강 → 청산 안 함


def test_exit_emit_idempotent(con):
    from src.trading import exits

    pos = {"code": "005930", "qty": 5, "plpc": -10}
    exits._emit_sell(con, pos, "손절 -10.0%")
    exits._emit_sell(con, pos, "손절 -10.5%")   # 같은 사유타입·날짜 → 멱등(1건)
    rows = con.execute("SELECT * FROM signals WHERE source='exit'").fetchall()
    assert len(rows) == 1 and rows[0]["action"] == "sell" and rows[0]["qty"] == 5


def test_signal_entry(con, monkeypatch):
    from src.trading import signal_entry

    monkeypatch.setenv("SIGNAL_ENTRY_SYMBOL", "SPY")
    monkeypatch.setenv("SIGNAL_ENTRY_QTY", "2")
    monkeypatch.setenv("SIGNAL_ENTRY_SYMBOL_KR", "")   # KR 확장은 별도 케이스에서
    # 평시(green 아님) → 진입 없음
    monkeypatch.setattr(signal_entry, "vix_signal", lambda c: {"state": "neutral", "label": "평시"})
    assert signal_entry.check_entry(con) is None
    assert con.execute("SELECT COUNT(*) c FROM signals").fetchone()["c"] == 0
    # green → SPY 매수 신호 emit
    monkeypatch.setattr(signal_entry, "vix_signal", lambda c: {"state": "buy2", "label": "분할매수"})
    out = signal_entry.check_entry(con)
    assert out["symbol"] == "SPY" and out["qty"] == 2
    r = con.execute("SELECT ticker, action, source FROM signals").fetchall()
    assert (len(r) == 1 and r[0]["ticker"] == "SPY" and r[0]["action"] == "buy"
            and r[0]["source"] == "signal-entry")
    # 같은 날 재호출 → 멱등(1건)
    signal_entry.check_entry(con)
    assert con.execute("SELECT COUNT(*) c FROM signals").fetchone()["c"] == 1
    # dry → emit 안 함
    monkeypatch.setenv("SIGNAL_ENTRY_SYMBOL", "QQQ")
    assert signal_entry.check_entry(con, dry=True)["symbol"] == "QQQ"
    assert con.execute("SELECT COUNT(*) c FROM signals WHERE ticker='QQQ'").fetchone()["c"] == 0
    # KR 확장: KR 신호(VKOSPI≥30 & 낙폭 5%+)가 green + 장중이면 KODEX200 emit — US와 독립
    from src.trading.brokers import kiwoom
    monkeypatch.setenv("SIGNAL_ENTRY_SYMBOL_KR", "069500")
    monkeypatch.setattr(signal_entry, "kr_signal",
                        lambda c: {"state": "buy", "label": "KR 매수 구간"})
    monkeypatch.setattr(kiwoom.KiwoomBroker, "is_market_open", lambda self, t: True)
    out = signal_entry.check_entry(con)
    assert [x["symbol"] for x in out["entries"]] == ["QQQ", "069500"]
    assert con.execute("SELECT COUNT(*) c FROM signals WHERE ticker='069500'").fetchone()["c"] == 1
    monkeypatch.setattr(kiwoom.KiwoomBroker, "is_market_open", lambda self, t: False)
    con.execute("DELETE FROM signals")
    con.commit()
    out = signal_entry.check_entry(con)
    assert [x["symbol"] for x in out["entries"]] == ["QQQ"]
    # KR만 green (US 평시) → KR만 emit
    monkeypatch.setattr(signal_entry, "vix_signal", lambda c: {"state": "neutral", "label": "평시"})
    monkeypatch.setattr(kiwoom.KiwoomBroker, "is_market_open", lambda self, t: True)
    con.execute("DELETE FROM signals")
    con.commit()
    out = signal_entry.check_entry(con)
    assert [x["symbol"] for x in out["entries"]] == ["069500"]
    # KR 과열보류(hold_melt — 낙폭 미달) → emit 없음
    monkeypatch.setattr(signal_entry, "kr_signal",
                        lambda c: {"state": "hold_melt", "label": "과열 변동성"})
    con.execute("DELETE FROM signals")
    con.commit()
    assert signal_entry.check_entry(con) is None


def test_reconcile(con, monkeypatch):
    from src.trading import reconcile
    from src.trading.brokers import alpaca

    monkeypatch.setattr(alpaca, "configured", lambda: True)
    con.execute("INSERT INTO orders (client_order_id, broker, ticker, status, created_at) "
                "VALUES (?,?,?,?, datetime('now'))", ("coid1", "alpaca", "AAPL", "pending_new"))
    con.commit()
    monkeypatch.setattr(alpaca.AlpacaBroker, "order_status",
                        lambda self, c: {"status": "filled", "filled_qty": "1", "filled_avg_price": "300"})
    up = reconcile.reconcile(con)
    assert len(up) == 1 and up[0]["to"] == "filled"
    assert con.execute("SELECT status FROM orders WHERE client_order_id='coid1'"
                       ).fetchone()["status"] == "filled"
    assert reconcile.reconcile(con) == []   # 이미 종료상태 → 재폴링 안 함


def test_kiwoom_ratelimit_verify_then_retry(con, monkeypatch):
    """1700 거부 → 유령접수 확인 후에만 재시도 (실측: 거부 응답이어도 접수될 수 있음 → 이중주문 방지)."""
    from src.trading.brokers import kiwoom
    from src.trading.brokers.base import OrderRequest

    monkeypatch.setattr(kiwoom, "_token", lambda: "tok")
    monkeypatch.setattr(kiwoom.time, "sleep", lambda s: None)   # 테스트에선 대기 생략

    class R:
        def __init__(self, d):
            self._d, self.content = d, b"x"

        def json(self):
            return self._d

    posts = []

    # case 1: 1700 거부 후 유령접수 발견 → 재제출 없이 그 주문번호로 submitted
    def post_reject(url, **kw):
        posts.append(kw.get("headers", {}).get("api-id"))
        return R({"return_code": 5, "return_msg": "허용된 요청 개수를 초과하였습니다[1700:...]"})

    monkeypatch.setattr(kiwoom.requests, "post", post_reject)
    monkeypatch.setattr(kiwoom.KiwoomBroker, "_find_recent_order",
                        lambda self, req: {"ord_no": "0070001"})
    res = kiwoom.KiwoomBroker().submit_order(
        con, OrderRequest(ticker="035420", action="buy", qty=1, price=None, strategy=""),
        client_order_id="sig-rl-ghost", signal_id=None)
    assert res["ok"] and posts == ["kt10000"]                   # 주문 POST 1회뿐 (재제출 금지)
    assert "지연접수" in con.execute(
        "SELECT message FROM orders WHERE client_order_id='sig-rl-ghost'").fetchone()["message"]

    # case 2: 유령 없음 → 1회 재시도로 성공
    posts.clear()

    def post_then_ok(url, **kw):
        posts.append(kw.get("headers", {}).get("api-id"))
        if posts.count("kt10000") == 1:
            return R({"return_code": 5, "return_msg": "...[1700:...]"})
        return R({"return_code": 0, "ord_no": "0070002", "return_msg": "모의투자 매수주문완료"})

    monkeypatch.setattr(kiwoom.requests, "post", post_then_ok)
    monkeypatch.setattr(kiwoom.KiwoomBroker, "_find_recent_order", lambda self, req: None)
    res = kiwoom.KiwoomBroker().submit_order(
        con, OrderRequest(ticker="035420", action="buy", qty=1, price=None, strategy=""),
        client_order_id="sig-rl-retry", signal_id=None)
    assert res["ok"] and posts.count("kt10000") == 2


def test_reconcile_kiwoom_fill_and_watchdog(con, monkeypatch):
    """키움 체결 반영(kt00007 매칭) + 매도 미체결 워치독(취소→재제출 신호, 멱등)."""
    from src.trading import reconcile
    from src.trading.brokers import alpaca, kiwoom

    monkeypatch.setattr(alpaca, "configured", lambda: False)
    monkeypatch.setattr(kiwoom, "configured", lambda: True)
    con.execute("INSERT INTO orders (client_order_id, broker, ticker, action, qty, status, "
                "created_at, message) VALUES ('k-buy','kiwoom-mock','035720','buy',1,"
                "'submitted', datetime('now','localtime'), '0001111 접수')")
    con.execute("INSERT INTO orders (client_order_id, broker, ticker, action, qty, status, "
                "created_at, message) VALUES ('k-sell','kiwoom-mock','035420','sell',1,"
                "'submitted', datetime('now','localtime','-10 minutes'), '0002222 접수')")
    con.execute("INSERT INTO orders (client_order_id, broker, ticker, action, qty, status, "
                "created_at, message) VALUES ('k-cxl','kiwoom-mock','005380','buy',1,"
                "'submitted', datetime('now','localtime','-5 minutes'), '0003333 접수')")
    con.commit()
    hist = [
        {"ord_no": "0001111", "code": "035720", "side": "buy", "qty": 1, "filled": 1,
         "remain": 0, "price": 36100, "tm": "10:00:00", "mdfy": "일반", "name": "카카오"},
        {"ord_no": "0002222", "code": "035420", "side": "sell", "qty": 1, "filled": 0,
         "remain": 1, "price": 0, "tm": "10:00:01", "mdfy": "일반", "name": "NAVER"},
        # 취소 실측 시그니처: mdfy '일반'인 채 체결 0·미체결 0 (전량취소로 소멸)
        {"ord_no": "0003333", "code": "005380", "side": "buy", "qty": 1, "filled": 0,
         "remain": 0, "price": 0, "tm": "10:00:02", "mdfy": "일반", "name": "현대차"},
    ]
    canceled = []
    monkeypatch.setattr(kiwoom.KiwoomBroker, "order_history", lambda self, ord_dt=None: hist)
    monkeypatch.setattr(kiwoom.KiwoomBroker, "is_market_open", lambda self, t: True)
    monkeypatch.setattr(kiwoom.KiwoomBroker, "cancel_order",
                        lambda self, o, s, qty=0: canceled.append(o) or {"ok": True, "msg": ""})
    monkeypatch.setattr(reconcile, "_alert", lambda text: None)

    up = reconcile.reconcile(con)
    st = {u["coid"]: u["to"] for u in up}
    assert st == {"k-buy": "filled", "k-sell": "stale_replaced", "k-cxl": "canceled"}
    assert canceled == ["0002222"]                              # 재제출 전 원주문 취소
    sig = con.execute("SELECT ticker, action FROM signals WHERE source='sell-retry'").fetchone()
    assert sig["ticker"] == "035420" and sig["action"] == "sell"
    assert reconcile.reconcile(con) == []                       # 재실행 멱등


def test_watchdog_alert_once(con, monkeypatch):
    """상호 감시 — 정체 시 경보 1회(쿨다운 중복 방지), 회복 시 무경보."""
    from src import notify
    from src.jobs import watchdog

    con.execute("CREATE TABLE collector_runs "
                "(collector TEXT, run_at TEXT, status TEXT, rows INT, message TEXT)")
    con.commit()
    sent = []
    monkeypatch.setattr(notify, "send", lambda t: sent.append(t) or True)

    assert watchdog.check_engine(con) == 1          # 하트비트 없음 → 경보
    assert watchdog.check_engine(con) == 0          # 쿨다운 내 재호출 → 무경보
    assert len(sent) == 1 and "엔진" in sent[0]

    con.execute("INSERT INTO collector_runs VALUES "
                "('sentiment', datetime('now','localtime'), 'ok', 1, NULL)")
    con.commit()
    assert watchdog.check_hourly(con) == 0          # 수집 정상 → 무경보
    assert len(sent) == 1


def test_leader_rotation(con, monkeypatch):
    """로테이션: top10 진입 → 주간 멱등 → 이탈(rank>30)·교체 → dry 무변경."""
    import pandas as pd

    from src.trading import leader_rotation as rot
    from src.trading.brokers import alpaca

    monkeypatch.setattr(alpaca, "configured", lambda: False)
    monkeypatch.setenv("ROTATION_SLOT_USD", "100")
    syms = [f"S{i:02d}" for i in range(1, 13)]
    ranks = pd.Series({s: float(i + 1) for i, s in enumerate(syms)})
    px = pd.Series({s: 100.0 for s in syms})
    monkeypatch.setattr(rot, "_ranks", lambda c, market="US": (ranks, px))

    res = rot.evaluate(con)
    assert len(res["enters"]) == 10 and res["exits"] == []
    assert con.execute("SELECT COUNT(*) c FROM rotation_slots").fetchone()["c"] == 10
    assert con.execute("SELECT COUNT(*) c FROM signals WHERE source='rotation' "
                       "AND action='buy'").fetchone()["c"] == 10
    assert rot.evaluate(con).get("skipped")            # 같은 주 재실행 → 멱등 스킵

    # 다음 주 시뮬레이션: S01 순위 이탈(40위) → 매도 + S11 교체 진입
    con.execute("DELETE FROM rotation_meta WHERE key='last_week_US'")
    ranks2 = ranks.copy()
    ranks2["S01"], ranks2["S11"] = 40.0, 1.0
    monkeypatch.setattr(rot, "_ranks", lambda c, market="US": (ranks2, px))
    res2 = rot.evaluate(con)
    assert [e["symbol"] for e in res2["exits"]] == ["S01"]
    assert any(e["symbol"] == "S11" for e in res2["enters"])
    assert con.execute("SELECT COUNT(*) c FROM rotation_slots").fetchone()["c"] == 10
    assert con.execute("SELECT COUNT(*) c FROM signals WHERE source='rotation' "
                       "AND action='sell'").fetchone()["c"] == 1

    con.execute("DELETE FROM rotation_meta WHERE key='last_week_US'")
    before = con.execute("SELECT COUNT(*) c FROM signals").fetchone()["c"]
    rot.evaluate(con, dry=True)                        # dry → 신호/장부 무변경
    assert con.execute("SELECT COUNT(*) c FROM signals").fetchone()["c"] == before


def test_leader_rotation_kr(con, monkeypatch):
    """KR 로테이션: 정수 주 사이징(비싼 종목 스킵) + 장외 defer + 장중 진입, US와 주간키 분리."""
    import pandas as pd

    from src.trading import leader_rotation as rot
    from src.trading.brokers import alpaca, kiwoom

    monkeypatch.setattr(alpaca, "configured", lambda: False)
    monkeypatch.setattr(kiwoom, "configured", lambda: False)
    monkeypatch.setenv("ROTATION_SLOT_KRW", "2000000")
    codes = [f"{100000 + i}" for i in range(12)]                 # 6자리 = KR
    ranks = pd.Series({c: float(i + 1) for i, c in enumerate(codes)})
    px = pd.Series({c: 1_500_000.0 for c in codes})
    px[codes[0]] = 3_000_000.0                                   # rank1이 슬롯보다 비쌈 → 스킵
    monkeypatch.setattr(rot, "_ranks", lambda c, market="US": (ranks, px))

    monkeypatch.setattr(kiwoom.KiwoomBroker, "is_market_open", lambda self, t: False)
    assert rot.evaluate(con, market="KR").get("deferred")        # 장외 → 보류(주간키 미소모)

    monkeypatch.setattr(kiwoom.KiwoomBroker, "is_market_open", lambda self, t: True)
    res = rot.evaluate(con, market="KR")
    syms = [e["symbol"] for e in res["enters"]]
    assert codes[0] not in syms and len(syms) == 9               # rank 2~10 진입 (비싼 1위 스킵)
    assert all(isinstance(e["qty"], int) and e["qty"] == 1 for e in res["enters"])
    assert rot.evaluate(con, market="KR").get("skipped")         # KR 주간 멱등
    assert con.execute("SELECT COUNT(*) c FROM rotation_slots").fetchone()["c"] == 9


def test_dashboard_auth(monkeypatch):
    import base64

    from flask import Flask

    from src.dashboard.auth import require_auth

    app = Flask(__name__)

    # DASH_PASS 미설정 → 인증 비활성 (로컬)
    monkeypatch.delenv("DASH_PASS", raising=False)
    with app.test_request_context("/"):
        assert require_auth() is None

    monkeypatch.setenv("DASH_USER", "admin")
    monkeypatch.setenv("DASH_PASS", "s3cret")

    # 자격 없음 → 401
    with app.test_request_context("/"):
        r = require_auth()
        assert r is not None and r.status_code == 401

    # 웹훅은 인증 예외 (TV는 Basic Auth 불가, 자체 시크릿)
    with app.test_request_context("/hook/tv", method="POST"):
        assert require_auth() is None

    # 정답 자격 → 통과
    tok = base64.b64encode(b"admin:s3cret").decode()
    with app.test_request_context("/", headers={"Authorization": f"Basic {tok}"}):
        assert require_auth() is None

    # 틀린 비밀번호 → 401
    bad = base64.b64encode(b"admin:wrong").decode()
    with app.test_request_context("/", headers={"Authorization": f"Basic {bad}"}):
        assert require_auth().status_code == 401


def test_engine_kill_switch(client, con, monkeypatch):
    _post(client, {"secret": "test-secret", "ticker": "AAPL", "action": "buy"})
    monkeypatch.setenv("KILL_SWITCH", "1")
    res = engine.process_once(con)
    assert res == {"processed": 0, "rejected": 1}
    assert con.execute("SELECT COUNT(*) n FROM orders").fetchone()["n"] == 0


def test_portfolio_snapshot_upsert(con, monkeypatch):
    """같은 날 두 번 스냅샷 → 브로커당 1행 (마지막 값 승리)."""
    from src.trading import portfolio
    from src.trading.brokers import alpaca, kiwoom

    monkeypatch.setattr(kiwoom, "configured", lambda: True)
    monkeypatch.setattr(alpaca, "configured", lambda: False)
    bal = {"cash": 500_000_000, "pur": 20e6, "value": 21e6, "pl": 1e6, "plpc": 5.0,
           "holdings": []}
    monkeypatch.setattr(kiwoom.KiwoomBroker, "account_balance", lambda self: dict(bal))
    assert portfolio.snapshot(con) == 1
    bal["cash"] = 501_000_000
    assert portfolio.snapshot(con) == 1
    rows = con.execute("SELECT * FROM portfolio_snapshots").fetchall()
    assert len(rows) == 1 and rows[0]["equity"] == 501_000_000
    assert rows[0]["cash"] == 501_000_000 - 21e6


def test_daily_loss_gate(con, monkeypatch):
    """일손실 한도: 초과 시 매수만 차단(매도 허용), 경보 1회, 비활성 시 통과."""
    from src import notify
    from src.trading import portfolio

    portfolio.ensure(con)
    con.execute("CREATE TABLE IF NOT EXISTS collector_runs "
                "(collector TEXT, run_at TEXT, status TEXT, rows INT, message TEXT)")
    con.execute("INSERT INTO portfolio_snapshots VALUES ('2000-01-01','kiwoom',500e6,480e6,0)")
    monkeypatch.setenv("MAX_DAILY_LOSS_PCT", "2")
    sent = []
    monkeypatch.setattr(notify, "send", lambda t: sent.append(t))
    risk._eq_cache.clear()

    buy = {"ticker": "069500", "action": "buy", "qty": 1, "price": None}
    monkeypatch.setattr(risk, "_live_equity", lambda b: 485e6)   # -3%
    ok, why = risk.check(con, buy)
    assert not ok and "일손실" in why
    ok, _ = risk.check(con, {"ticker": "069500", "action": "sell", "qty": 1, "price": None})
    assert ok                                                    # 매도는 항상 통과
    risk.check(con, buy)
    assert len(sent) == 1                                        # 경보는 쿨다운 내 1회
    monkeypatch.setattr(risk, "_live_equity", lambda b: 495e6)   # -1% (한도 내)
    ok, _ = risk.check(con, buy)
    assert ok
    monkeypatch.setattr(risk, "_live_equity", lambda b: 400e6)   # -20%지만 비활성
    monkeypatch.setenv("MAX_DAILY_LOSS_PCT", "0")
    ok, _ = risk.check(con, buy)
    assert ok


def test_telegram_kill_switch(con, monkeypatch):
    """/킬스위치 on → DB 킬 → 리스크 게이트 차단 (전 프로세스 공유), off → 재개."""
    from src import db as db_mod
    from src.trading import telegram_cmd

    monkeypatch.setattr(db_mod, "connect", lambda: _NoClose(con))
    monkeypatch.delenv("KILL_SWITCH", raising=False)

    out = telegram_cmd.handle("/킬스위치 on")
    assert "ON" in out
    ok, why = risk.check(con, {"ticker": "AAPL", "action": "buy", "qty": 1, "price": None})
    assert not ok and "킬스위치" in why
    out = telegram_cmd.handle("/킬스위치")            # 상태 조회
    assert "ON" in out
    out = telegram_cmd.handle("/킬스위치 off")
    assert "OFF" in out
    ok, _ = risk.check(con, {"ticker": "AAPL", "action": "buy", "qty": 1, "price": None})
    assert ok
    assert "명령" in telegram_cmd.handle("/도움말")    # 미지원/도움말 → 목록


def test_event_alerts(con, monkeypatch):
    """지표 발표(actual 확인·30분 대기·멱등) + 실적 시간대 알림."""
    from datetime import datetime

    from src import notify
    from src.jobs import event_alerts

    con.execute("CREATE TABLE IF NOT EXISTS collector_runs "
                "(collector TEXT, run_at TEXT, status TEXT, rows INT, message TEXT)")
    con.execute("CREATE TABLE econ_calendar (date TEXT, gmt TEXT, country TEXT, event TEXT, "
                "actual TEXT, consensus TEXT, previous TEXT, major INTEGER)")
    con.execute("CREATE TABLE earnings_calendar (symbol TEXT, date TEXT, when_time TEXT, "
                "name TEXT, eps_forecast TEXT)")
    # CPI: GMT 13:30 → KST 22:30. actual 비어 있음
    con.execute("INSERT INTO econ_calendar VALUES "
                "('2026-07-23','13:30','US','CPI (YoY)','','3.1%','3.4%',1)")
    con.execute("INSERT INTO earnings_calendar VALUES "
                "('AMD','2026-07-23','time-after-hours','Advanced Micro','0.92')")
    con.commit()
    sent = []
    monkeypatch.setattr(notify, "send", lambda t: sent.append(t))
    monkeypatch.setattr(event_alerts, "_refresh_econ", lambda c, d: (
        c.execute("UPDATE econ_calendar SET actual='3.2%' WHERE event='CPI (YoY)'"), c.commit()))

    # 발표 5분 후 → 재조회로 actual 확보 → 알림 1건
    now = datetime.fromisoformat("2026-07-23T22:35:00")
    assert event_alerts.check(con, now) == 1
    assert "3.2%" in sent[0] and "3.1%" in sent[0]
    assert event_alerts.check(con, now) == 0            # 멱등
    # 실적: AMC → 익일 05:00 KST 이후 알림
    now2 = datetime.fromisoformat("2026-07-24T05:10:00")
    assert event_alerts.check(con, now2) == 0            # AMD가 감시목록에 없으면 0
    con.execute("CREATE TABLE IF NOT EXISTS rotation_slots (symbol TEXT)")
    con.execute("INSERT INTO rotation_slots VALUES ('AMD')")
    con.commit()
    assert event_alerts.check(con, now2) == 1
    assert "AMD" in sent[-1] and "0.92" in sent[-1]
    assert event_alerts.check(con, now2) == 0            # 멱등
