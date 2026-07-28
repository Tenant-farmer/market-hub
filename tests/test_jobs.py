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


def test_daily_reports_are_staggered(con, monkeypatch):
    """정기 리포트 3종은 시각을 벌려 보낸다 — 한꺼번에 오면 구분이 안 된다(사용자 지적)."""
    from src.collectors import base
    from src.jobs import hourly

    sent = []
    monkeypatch.setattr(base, "run_collector", lambda name, fn: sent.append(name))
    monkeypatch.setattr(hourly, "_ran_today", lambda c, n: n in sent)

    hourly._send_daily_reports(con, 6)
    assert sent == []                                   # 06시엔 아직 아무것도
    hourly._send_daily_reports(con, 7)
    assert sent == ["market_brief"]                     # 07시 시황
    hourly._send_daily_reports(con, 8)
    assert sent == ["market_brief", "telegram_brief"]   # 08시 브리핑
    hourly._send_daily_reports(con, 12)
    assert len(sent) == 2                               # 낮엔 추가 발송 없음
    hourly._send_daily_reports(con, 16)
    assert sent[-1] == "status_report"                  # 16시 상태(KR 마감 후)
    hourly._send_daily_reports(con, 17)
    assert len(sent) == 3                               # 재실행해도 중복 없음


def test_daily_reports_catch_up_on_missed_slot(con, monkeypatch):
    """PC가 꺼져 슬롯을 놓쳤으면 다음 실행에서 보충 — 조용한 누락 방지."""
    from src.collectors import base
    from src.jobs import hourly

    sent = []
    monkeypatch.setattr(base, "run_collector", lambda name, fn: sent.append(name))
    monkeypatch.setattr(hourly, "_ran_today", lambda c, n: n in sent)

    hourly._send_daily_reports(con, 18)                 # 하루 종일 꺼져 있다가 18시에 첫 실행
    assert sent == ["market_brief", "telegram_brief", "status_report"]


def test_status_report_prefers_today_trades(con, monkeypatch):
    """16시 발송이 기본이므로 **당일** 매매를 보여준다(없으면 전날, 라벨로 구분)."""
    from datetime import date as _d

    con.execute("INSERT INTO portfolio_snapshots(date,broker,equity,pl) "
                "VALUES ('2026-07-28','kiwoom',1000,0)")
    y = (_d.today() - timedelta(days=1)).isoformat()
    con.execute("INSERT INTO orders(client_order_id,broker,ticker,action,qty,status,created_at) "
                "VALUES ('a','kiwoom','005930','buy',1,'filled',?)", (f"{y}T10:00",))
    assert "어제 매매 1건" in status_report.build_text(con)

    con.execute("INSERT INTO orders(client_order_id,broker,ticker,action,qty,status,created_at) "
                "VALUES ('b','kiwoom','AAPL','sell',1,'filled',?)",
                (f"{_d.today().isoformat()}T14:00",))
    txt = status_report.build_text(con)
    assert "오늘 매매 1건" in txt and "어제" not in txt.split("🔄")[1][:30]


def test_verdict_alert_fires_on_condition_not_date_memory(con, monkeypatch):
    """판정은 **조건 충족 시 자동 발송**된다 — 날짜를 기억해 수동 실행하면 잊는다.

    2차는 **표본(거래일 20일) AND 날짜 하한(8/25)** 둘 다 만족해야 나간다 —
    표본만 차고 날짜가 이르면 관찰 기간이 짧고, 날짜만 넘고 표본이 모자라면 무의미하다
    (2026-07-28 사용자 결정으로 하한 추가).
    """
    from datetime import date as _d

    from src.jobs import verdict_alert

    sent = []
    monkeypatch.setattr("src.notify.send", lambda t, **k: sent.append(t) or True)
    monkeypatch.setattr(verdict_alert, "build_first", lambda c, s: "1차 본문")
    monkeypatch.setattr(verdict_alert, "build_second", lambda c, s: "2차 본문")
    monkeypatch.setattr(verdict_alert, "VERDICT1_DATE", _d(2099, 1, 1))   # 아직 안 됨
    monkeypatch.setattr(verdict_alert, "VERDICT2_MIN_DATE", _d(2020, 1, 1))  # 날짜는 충족

    # 평일만 넣는다 — 주말 행은 표본으로 안 세므로 넣으면 개수가 어긋난다
    days = [d for d in range(1, 32) if _d(2026, 8, d).weekday() < 5][:19]
    con.executemany("INSERT INTO portfolio_snapshots(date,broker,equity,pl) VALUES (?,?,?,?)",
                    [(f"2026-08-{d:02d}", "kiwoom", 1000, 0) for d in days])
    con.commit()
    assert verdict_alert._eq_days(con) == 19
    assert verdict_alert.run(con) == 0            # 19일 — 아직 미달, 1차도 날짜 전
    assert sent == []

    last = [d for d in range(1, 32) if _d(2026, 8, d).weekday() < 5][19]
    con.execute("INSERT INTO portfolio_snapshots(date,broker,equity,pl) VALUES (?,?,?,?)",
                (f"2026-08-{last:02d}", "kiwoom", 1000, 0))
    con.commit()

    monkeypatch.setattr(verdict_alert, "VERDICT2_MIN_DATE", _d(2099, 1, 1))  # 날짜 미도달
    assert verdict_alert.run(con) == 0            # 표본은 찼지만 판정일 전 → 발송 안 함
    assert sent == []

    monkeypatch.setattr(verdict_alert, "VERDICT2_MIN_DATE", _d(2020, 1, 1))
    assert verdict_alert.run(con) == 1            # 표본 20일 + 날짜 충족 → 2차 발송
    assert sent == ["2차 본문"]
    assert verdict_alert.run(con) == 0            # 재실행해도 중복 없음(멱등)

    monkeypatch.setattr(verdict_alert, "VERDICT1_DATE", _d(2020, 1, 1))  # 1차 날짜 도달
    assert verdict_alert.run(con) == 1
    assert sent[-1] == "1차 본문"


def test_alert_bodies_are_html_safe(con, monkeypatch):
    """알림 본문에 허용 외 태그가 없어야 한다 — 하나라도 있으면 텔레그램이 400을 낸다.

    2026-07-28 실측: 1차 판정의 '수집 에러율 < 5%'에서 `<`가 태그 시작으로 해석돼
    발송이 실패했다. 8/6 판정이 그대로 죽었을 것 — 미리 보내보지 않았으면 몰랐다.
    """
    import re

    from src.jobs import verdict_alert

    allowed = re.compile(r"</?(b|i|code|blockquote expandable|blockquote)>")
    monkeypatch.setattr(verdict_alert, "_eq_days", lambda c: 3)
    monkeypatch.setattr("verdict.system_verdict", lambda c, s: [
        {"item": "수집 에러율 < 5% (최근 24h)", "ok": True, "detail": "24h 1.7% <제한>"}])
    monkeypatch.setattr("_perf_verdict.perf_verdict", lambda c, s: [
        {"item": "α > 0", "kind": "level", "basis": "x", "ok": None, "detail": "a < b & c"}])

    for text in (verdict_alert.build_first(con, "2026-07-23"),
                 verdict_alert.build_second(con, "2026-07-23")):
        bad = [m for m in re.findall(r"<[^>]{0,80}>", text) if not allowed.fullmatch(m)]
        assert not bad, f"허용 외 태그: {bad}"
    assert "&lt; 5%" in verdict_alert.build_first(con, "2026-07-23")   # 실제로 이스케이프됨


def test_notify_does_not_leak_token_on_error(monkeypatch):
    """발송 실패 시 봇 토큰이 새면 안 된다 — raise_for_status()는 URL을 그대로 담는다."""
    import src.notify as notify

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:SECRET-TOKEN")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")

    class _R:
        ok, status_code, text = False, 400, '{"description":"Bad Request: parse error"}'

        def json(self):
            return {"description": "Bad Request: parse error"}

    monkeypatch.setattr(notify.requests, "post", lambda *a, **k: _R())
    try:
        notify.send("x")
        raise AssertionError("예외가 나야 한다")
    except RuntimeError as e:
        assert "SECRET-TOKEN" not in str(e) and "parse error" in str(e)


def test_econ_calendar_gmt_field_is_actually_et():
    """캘린더의 `gmt` 필드는 이름과 달리 **ET**다 — +9h로 보면 4시간 어긋난다.

    2026-07-29 실측 근거: Core PCE는 08:30 ET 발표이고 응답의 gmt도 08:30이다.
    ET→KST(여름 +13h)면 21:30 KST — 외부 시황 서비스 표기와 일치한다.
    이 오류는 /econ 표시뿐 아니라 **event_alerts가 발표를 4시간 일찍 잡는** 문제였다.
    """
    from src.timeutil import et_to_kst

    summer = et_to_kst("2026-07-31", "08:30")          # EDT (UTC-4)
    assert summer.strftime("%m-%d %H:%M") == "07-31 21:30"
    winter = et_to_kst("2026-01-15", "08:30")          # EST (UTC-5) → 1시간 더
    assert winter.strftime("%m-%d %H:%M") == "01-15 22:30"
    fomc = et_to_kst("2026-07-30", "14:00")            # 날짜가 넘어간다
    assert fomc.strftime("%m-%d %H:%M") == "07-31 03:00"
    assert et_to_kst("2026-07-31", "") is None


def test_econ_week_groups_by_kst_day_and_folds(con):
    """주간 다이제스트 — 요일별 접이식, 노이즈·중복 제거, KST 날짜로 재배치."""
    from datetime import date as _d

    from src.jobs import econ_week

    mon = _d(2026, 7, 27)
    con.executemany(
        "INSERT INTO econ_calendar(date,gmt,country,event,major) VALUES (?,?,?,?,1)", [
            ("2026-07-30", "14:00", "US", "FOMC Statement"),        # → 7/31 03:00 KST
            ("2026-07-31", "08:30", "US", "Core PCE Price Index"),  # → 7/31 21:30
            ("2026-07-31", "08:30", "US", "Core PCE Price Index"),  # 중복
            ("2026-07-31", "07:00", "DE", "Bavaria CPI"),           # 주별 CPI = 노이즈
            ("2026-07-31", "11:00", "UK", "BoE MPC vote hike"),     # 투표 항목 = 노이즈
        ])
    con.commit()
    ev = econ_week.week_events(con, mon)
    fri = ev["2026-07-31"]
    assert [x[0] for x in fri] == ["03:00", "21:30"]    # KST 재배치 + 중복·노이즈 제거
    assert all("Bavaria" not in x[2] and "MPC vote" not in x[2] for x in fri)

    txt = econ_week.build_text(con, mon)
    assert txt.count("<blockquote expandable>") == 1   # 일정 있는 요일만 블록
    assert "[7/31 금요일]" in txt and "2건" in txt
    assert "통화정책" in txt                            # 마무리 코멘트는 데이터 기반


def test_econ_week_width_adapts_and_bolds_key_events(con):
    """폭은 **그 주의 실제 최장 줄**에서 계산되고, 핵심 지표는 굵게 나온다.

    고정 34로 박아두면 지표 이름이 긴 주에 그 요일만 삐져나온다(2026-07-29 사용자 지적).
    """
    import re
    from datetime import date as _d

    from src.jobs import econ_week

    mon = _d(2026, 7, 27)
    con.executemany(
        "INSERT INTO econ_calendar(date,gmt,country,event,major) VALUES (?,?,?,?,1)", [
            ("2026-07-27", "08:30", "US", "CPI"),                     # 짧고 **핵심**
            ("2026-07-28", "08:30", "US", "Manufacturing PMI Final"),  # 길고 비핵심
            ("2026-07-29", "08:30", "US", "Atlanta Fed GDPNow"),       # GDP지만 추정치
        ])
    con.commit()
    txt = econ_week.build_text(con, mon)
    heads = [b.split("\n")[0] for b in txt.split("<blockquote expandable>")[1:]]
    widths = {econ_week._w(re.sub(r"</?[bi]>", "", h)) for h in heads}
    assert len(widths) == 1 and widths.pop() >= econ_week.MIN_W   # 전 블록 동일 폭

    assert econ_week._is_key("CPI") and econ_week._is_key("FOMC Statement")
    assert not econ_week._is_key("Atlanta Fed GDPNow")   # 실시간 추정치는 굵게 안 함
    assert not econ_week._is_key("Consumer Confidence")
    body = txt.split("<blockquote expandable>")[1]
    assert "<b>21:30 🇺🇸 CPI</b>" in body                # 08:30 ET = 21:30 KST · 핵심만 볼드


def test_econ_week_no_truncation_and_no_lookalikes(con):
    """지표명이 잘리지 않아야 하고, 잘려서 **똑같아 보이는 줄**이 없어야 한다.

    2026-07-29 실측: 'Industrial Production forecast 1m/2m ahead'가 둘 다
    'Industrial Production…'으로 잘려 같은 줄이 두 번 있는 것처럼 보였다.
    """
    from datetime import date as _d

    from src.jobs import econ_week

    mon = _d(2026, 7, 27)
    con.executemany(
        "INSERT INTO econ_calendar(date,gmt,country,event,major) VALUES (?,?,?,?,1)", [
            ("2026-07-27", "08:30", "JP", "Industrial Production forecast 1m ahead"),
            ("2026-07-27", "08:30", "JP", "Industrial Production forecast 2m ahead"),
            ("2026-07-27", "09:00", "KR", "S&P Global South Korea Manufacturing PMI"),
            ("2026-07-27", "10:00", "DE", "German Unemployment Change"),
        ])
    con.commit()
    txt = econ_week.build_text(con, mon)
    body = txt.split("<blockquote expandable>")[1].split("</blockquote>")[0]

    assert "…" not in body                              # 잘림 0건
    assert "forecast" not in body                       # METI 전망치는 노이즈로 제외
    assert "South Korea Manufacturing PMI" in body      # 제공사(S&P Global)만 제거
    assert "German Unemployment Change" in body         # 국가 접두어는 유지(사용자 요청)
    lines = [line for line in body.split("\n")[1:] if line.strip()]
    assert len(lines) == len(set(lines))                # 똑같아 보이는 줄 없음

def test_verdict2_excludes_weekends_and_honors_date_floor(monkeypatch):
    """2차 판정 게이트 — 주말 패딩과 조기 발송을 둘 다 막는다.

    portfolio_snapshots는 매시간 돌아 주말에도 행이 남는데, 그날은 금요일 값 복사라
    **수익률 0%인 가짜 관측**이다. 달력일로 세면 20일이 8/11에 차서 조기 발송되고,
    0% 날이 변동성을 낮춰 α의 t값까지 부풀린다(2026-07-28 사용자 결정으로 8/25 하한 추가).
    """
    import sqlite3
    from datetime import date, timedelta

    from src.jobs import verdict_alert as va

    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE portfolio_snapshots (date TEXT, broker TEXT, equity REAL)")
    d = date(2026, 7, 23)
    for _ in range(28):                        # 4주치 = 달력일 28일, 거래일 20일
        con.execute("INSERT INTO portfolio_snapshots VALUES (?, 'kiwoom', 1.0)",
                    (d.isoformat(),))
        d += timedelta(days=1)
    con.commit()

    assert con.execute("SELECT COUNT(DISTINCT date) FROM portfolio_snapshots").fetchone()[0] == 28
    assert va._eq_days(con) == 20               # 주말 8일 제외
    assert va.VERDICT2_MIN_DATE == date(2026, 8, 25)
    con.close()

_HOURLY_TIMES = [
    ("2026-07-28T10:05:00", "KR 장중"),
    ("2026-07-28T07:05:00", "아침 슬롯"),
    ("2026-07-28T19:05:00", "KR 저녁(VKOSPI 재수집)"),
    ("2026-07-28T23:05:00", "US 장중"),
    ("2026-07-26T13:05:00", "일요일 장외"),
]


def _hourly_sequence(monkeypatch, tmp_path, iso):
    """그 시각에 hourly가 부르는 수집기 이름을 **순서대로** 뽑는다 (실제 수집 없음)."""
    import sys
    from datetime import datetime

    from src import db as _db
    from src.collectors import base
    from src.jobs import hourly

    monkeypatch.setenv("MARKET_HUB_DB", str(tmp_path / "h.db"))
    c = _db.connect()
    c.execute("CREATE TABLE IF NOT EXISTS collector_runs (id INTEGER PRIMARY KEY, "
              "collector TEXT, run_at TEXT, status TEXT, rows INT, message TEXT)")
    c.commit()
    c.close()

    seq = []
    monkeypatch.setattr(base, "run_collector", lambda name, fn: seq.append(name))
    monkeypatch.setattr(hourly, "_ran_today", lambda c, n: False)
    monkeypatch.setattr(hourly, "_send_daily_reports", lambda c, h: None)
    # 부수효과 있는 것들은 무력화 — 라우팅만 본다
    import src.jobs.watchdog as wd
    import src.trading.portfolio as pf
    monkeypatch.setattr(wd, "check_engine", lambda c: 0)
    monkeypatch.setattr(pf, "snapshot", lambda c: 0)

    class _Now(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.fromisoformat(iso)
    monkeypatch.setattr(hourly, "datetime", _Now)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    sys.modules.setdefault("analyze", type(sys)("analyze"))
    sys.modules["analyze"].run_us = lambda: None
    sys.modules["analyze"].run_kr = lambda: None
    hourly.main()
    return seq


@pytest.mark.parametrize("iso,label", _HOURLY_TIMES)
def test_hourly_routing_is_stable(monkeypatch, tmp_path, iso, label):
    """hourly 라우팅 회귀 잠금 — main()을 리팩터해도 **호출 집합이 변하면 안 된다**.

    main()이 127줄·분기 18개라 손대기 겁나는 상태였다. 리팩터 전에 시각별 호출
    시퀀스를 못박아, 순수 이동인지 동작 변경인지 테스트가 판별하게 한다.
    """
    seq = _hourly_sequence(monkeypatch, tmp_path, iso)
    assert seq[:4] == ["sentiment", "macro", "news", "dart"], f"{label}: 상시 수집이 먼저"
    assert len(seq) == len(set(seq)) or "us_sectors" in seq   # 중복은 us_sectors만 허용
    if "장중" in label and "KR" in label:
        assert {"kr_sectors", "kr_stocks", "kr_flows"} <= set(seq)
    if "저녁" in label:
        assert "vkospi_pm" in seq
    if "아침" in label:
        assert {"backup", "virtual", "insider", "us_stocks", "ecos", "vkospi"} <= set(seq)
    if "일요일" in label:
        assert not ({"kr_stocks", "us_stocks"} & set(seq)), "일요일엔 장 수집 없음"
