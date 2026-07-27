"""jobs 본문 생성 테스트 — 텔레그램으로 나가는 글이 맞게 만들어지는가.

왜 필요한가: jobs는 무인 가동 중 사용자가 보는 **유일한 창**인데 10개 모듈 중 2개만
테스트가 있었다. 2026-07-27 trade_alerts가 SQL 컬럼 누락으로 0건을 조용히 반환한 사고가
정확히 이 사각지대였다(IndexError가 `except: pass`에 삼켜짐).

원칙: notify.send는 가로채고 **본문 문자열만** 검증한다. 실제 발송·네트워크 없음.
"""
import sqlite3
from datetime import date, timedelta

import pytest

from src.jobs import market_brief, status_report


@pytest.fixture
def con():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE prices_daily (symbol TEXT, market TEXT, date TEXT, open REAL, "
              "high REAL, low REAL, close REAL, volume REAL, value REAL, "
              "PRIMARY KEY (symbol, date))")
    c.execute("CREATE TABLE news (title TEXT, url TEXT, source TEXT, dt TEXT, symbol TEXT)")
    c.execute("CREATE TABLE econ_calendar (date TEXT, gmt TEXT, country TEXT, event TEXT, "
              "actual TEXT, consensus TEXT, previous TEXT, major INTEGER DEFAULT 0)")
    c.execute("CREATE TABLE earnings_calendar (symbol TEXT, date TEXT, when_time TEXT, "
              "name TEXT, eps_forecast TEXT)")
    c.execute("CREATE TABLE portfolio_snapshots (date TEXT, broker TEXT, equity REAL, "
              "pl REAL, PRIMARY KEY (date, broker))")
    c.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY AUTOINCREMENT, client_order_id TEXT, "
              "signal_id INTEGER, broker TEXT, ticker TEXT, action TEXT, qty REAL, "
              "status TEXT, message TEXT, created_at TEXT)")
    c.execute("CREATE TABLE signals (id INTEGER PRIMARY KEY AUTOINCREMENT, hash TEXT, "
              "received_at TEXT, source TEXT, ticker TEXT, action TEXT, qty REAL, "
              "strategy TEXT, raw TEXT, status TEXT)")
    c.execute("CREATE TABLE collector_runs (collector TEXT, run_at TEXT, status TEXT, "
              "rows INTEGER, message TEXT)")
    c.execute("CREATE TABLE rotation_slots (symbol TEXT PRIMARY KEY, qty REAL, entered TEXT)")
    c.execute("CREATE TABLE daytrade_equity (strategy TEXT, date TEXT, equity REAL, n_open INT)")
    c.execute("CREATE TABLE daytrade_ledger (strategy TEXT, status TEXT, pnl_pct REAL)")
    yield c
    c.close()


def _px(con, sym, pairs):
    con.executemany("INSERT INTO prices_daily(symbol,date,close) VALUES (?,?,?)",
                    [(sym, d, v) for d, v in pairs])


# ------------------------------------------------------------------ 시황 브리핑
def test_market_brief_quote_and_direction(con):
    _px(con, "^GSPC", [("2026-07-24", 6000.0), ("2026-07-27", 6060.0)])
    cur, chg, pct = market_brief._quote(con, "^GSPC")
    assert (cur, chg) == (6060.0, 60.0) and round(pct, 2) == 1.0
    assert market_brief._quote(con, "NOPE") is None          # 2거래일 미만이면 None

    line = market_brief._line("S&P 500", (6060.0, 60.0, 1.0), 2)
    assert "🔺" in line and "6,060.00" in line and "+1.00%" in line
    assert "🔽" in market_brief._line("WTI", (60.0, -1.5, -2.4), 2)


def test_market_brief_cut_normalizes_ellipsis():
    """뉴스 제공처가 이미 '...'로 자른 제목 — 말줄임이 겹치면 안 된다."""
    assert market_brief._cut("짧은 제목", 40) == "짧은 제목"
    assert market_brief._cut("이미 잘린 제목입니다...", 40) == "이미 잘린 제목입니다…"
    long = "가" * 80
    out = market_brief._cut(long, 30)
    assert len(out) <= 31 and out.endswith("…")
    # 단어 중간에서 자르지 않기 — 공백 경계 우선
    assert market_brief._cut("alpha beta gamma delta epsilon zeta", 24).endswith("…")


def test_market_brief_issues_fomc_priority_and_dedupe(con):
    """FOMC는 2일 창을 벗어나도 잡아야 하고(실측: 7/30 FOMC 누락), 중복 이벤트는 1건만."""
    today = date.today()
    d5 = (today + timedelta(days=5)).isoformat()
    con.executemany(
        "INSERT INTO econ_calendar(date,country,event,major) VALUES (?,?,?,?)",
        [(d5, "US", "FOMC Interest Rate Decision", 1),
         (today.isoformat(), "US", "GDPNow", 1),
         ((today + timedelta(days=1)).isoformat(), "US", "GDPNow", 1)])    # 같은 이벤트 반복
    issues = market_brief._issues(con)
    assert any("FOMC" in i for i in issues)                  # 7일 창에서 잡힘
    assert sum("GDPNow" in i for i in issues) == 1           # 중복 제거


def test_market_brief_reports_missing_symbols(con):
    """수집 실패 심볼을 조용히 빼지 말고 '미수집'으로 드러낼 것."""
    _px(con, "^DJI", [("2026-07-24", 44000.0), ("2026-07-27", 44100.0)])
    txt = market_brief.build_text(con)
    assert "DOW" in txt and "미수집" in txt and "NASDAQ" in txt.split("미수집")[1]


# ------------------------------------------------------------------ 상태 리포트
def test_status_report_sections_and_account(con, monkeypatch):
    con.executemany(
        "INSERT INTO portfolio_snapshots(date,broker,equity,pl) VALUES (?,?,?,?)",
        [("2026-07-24", "kiwoom", 500_000_000, 800_000),
         ("2026-07-27", "kiwoom", 505_000_000, 1_200_000)])
    _px(con, "KRW=X", [("2026-07-24", 1380.0), ("2026-07-27", 1385.0)])
    con.execute("INSERT INTO rotation_slots(symbol,qty,entered) VALUES ('AAPL',5,'2026-07-20')")
    con.execute("INSERT INTO rotation_slots(symbol,qty,entered) VALUES ('005930',10,'2026-07-20')")
    monkeypatch.setattr("src.analytics.thesis.check_theses", lambda c: [
        {"strategy": "로테이션", "status": "ok", "detail": ""},
        {"strategy": "손절", "status": "broken", "detail": "보유 종목이 손절선 아래인데 미청산"},
    ])
    txt = status_report.build_text(con)

    assert "505,000,000원" in txt and "+1.00%" in txt        # 전일 대비 변화율
    assert "US 1슬롯" in txt and "KR 1슬롯" in txt           # 6자리 숫자만 KR로 분류
    assert "전략 전제 1/2" in txt and "미청산" in txt        # 깨진 전제를 본문에 노출
    assert "🎯 검증 진행" in txt


def test_status_report_flags_error_burst(con):
    """수집 에러가 24h 5건 초과면 이상 징후에, 단 '해소 추정'을 구분해 표기."""
    old = (date.today() - timedelta(hours=0)).isoformat()
    con.executemany(
        "INSERT INTO collector_runs(collector,run_at,status) VALUES (?,?,'error')",
        [("news", f"{old} 03:0{i}:00") for i in range(8)])
    con.execute("INSERT INTO portfolio_snapshots(date,broker,equity,pl) "
                "VALUES ('2026-07-27','kiwoom',1,0)")
    txt = status_report.build_text(con)
    assert "⚠ 이상 징후" in txt and "수집 에러 24h 8건" in txt


def test_status_report_send_uses_notify(con, monkeypatch):
    """발송 경로가 실제로 notify를 타는지 — 본문 생성만 되고 안 나가는 사고 방지."""
    sent = []
    monkeypatch.setattr("src.notify.send", lambda t, **k: sent.append(t))
    con.execute("INSERT INTO portfolio_snapshots(date,broker,equity,pl) "
                "VALUES ('2026-07-27','kiwoom',1000,0)")
    assert status_report.send_report(con) == 1
    assert len(sent) == 1 and "상태 리포트" in sent[0]


# ------------------------------------------------------------------ 전제 점검 (thesis)
def test_thesis_failed_check_stays_visible(con, monkeypatch):
    """실사고 회귀: 점검이 예외로 죽으면 **항목이 사라져** '전제 n/n 유효 ✅'가 됐다.

    상태 리포트는 len(결과)로 n을 계산하므로, 손절 감시가 꺼져도 초록불이 뜬다 —
    감시 장치가 조용히 무력화되는 최악의 형태. 실패는 warn으로 남아야 한다.
    """
    from src.analytics import thesis

    monkeypatch.setattr("src.errlog.swallow", lambda *a, **k: None)   # 로그 I/O 차단
    monkeypatch.setattr("src.dashboard.queries_macro.kr_signal",
                        lambda c: (_ for _ in ()).throw(RuntimeError("VKOSPI 조회 실패")))
    monkeypatch.setattr("src.trading.brokers.kiwoom.configured", lambda: True)
    monkeypatch.setattr("src.trading.brokers.kiwoom.KiwoomBroker",
                        lambda: (_ for _ in ()).throw(RuntimeError("토큰 만료")))

    out = thesis.check_theses(con)
    by = {t["strategy"]: t for t in out}
    assert "KR 신호진입" in by and by["KR 신호진입"]["status"] == "warn"
    assert "청산 규칙" in by and by["청산 규칙"]["status"] == "warn"
    assert "판정 불가" in by["청산 규칙"]["detail"]
    assert all(t["status"] != "ok" or t["strategy"] not in ("KR 신호진입", "청산 규칙")
               for t in out)                       # 실패가 ok로 둔갑하지 않는다


def test_thesis_balance_none_is_not_silent(con, monkeypatch):
    """예외가 아니라 None이 오는 경로(키움 내부에서 삼킴)도 항목을 남겨야 한다."""
    from src.analytics import thesis

    class _B:
        def account_balance(self):
            return None
    monkeypatch.setattr("src.trading.brokers.kiwoom.configured", lambda: True)
    monkeypatch.setattr("src.trading.brokers.kiwoom.KiwoomBroker", _B)

    by = {t["strategy"]: t for t in thesis.check_theses(con)}
    assert by["청산 규칙"]["status"] == "warn" and "잔고 조회 실패" in by["청산 규칙"]["detail"]


def test_kr_signal_reports_staleness(con):
    """묵은 VKOSPI로 신호가 도는 걸 드러내야 한다 (2026-07-27: 게시 지연으로 1거래일 밀림)."""
    from src.dashboard.queries_macro import kr_signal

    today = date.today()
    _px(con, "VKOSPI", [((today - timedelta(days=9)).isoformat(), 35.0)])
    _px(con, "1001", [((today - timedelta(days=i)).isoformat(), 3000.0 - i)
                      for i in range(260, 0, -1)])
    sig = kr_signal(con)
    assert sig["stale_days"] == 9                       # 신호에 신선도가 실려 나온다

    from src.analytics import thesis
    by = {t["strategy"]: t for t in thesis.check_theses(con)}
    assert by["KR 신호진입"]["status"] == "warn" and "묵음" in by["KR 신호진입"]["detail"]
