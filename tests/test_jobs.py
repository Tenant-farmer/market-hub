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
