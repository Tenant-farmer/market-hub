"""수집기 파싱·정규화 테스트 — 네트워크 없이 골든 샘플로 검증.

왜 이 파일이 생겼나: 2026-07-27 하루에 '조용히 틀린 값' 버그가 2건 나왔고 둘 다
수집·기록 경로였다(체결가 지수표기, VKOSPI 이름 오매칭). trading은 14/14 모듈이
테스트를 갖고 있어 버그가 잡혔지만 collectors는 22개 중 3개뿐이었다.

원칙: **응답 → 정규화 행**만 검증한다(HTTP는 monkeypatch). 실제 API를 때리지 않으므로
빠르고, 소스 스키마가 바뀌면 골든 샘플을 갱신하는 것으로 대응한다.
"""
import sqlite3
from datetime import date, timedelta

import pytest

from src.collectors import earnings, econ_calendar, fed, insider, sentiment, vkospi


@pytest.fixture
def con():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE prices_daily (symbol TEXT, market TEXT, date TEXT, open REAL, "
              "high REAL, low REAL, close REAL, volume REAL, value REAL, "
              "PRIMARY KEY (symbol, date))")
    c.execute("CREATE TABLE sector_map (stock_code TEXT, sector_code TEXT, market TEXT, name TEXT)")
    c.execute("CREATE TABLE sentiment_daily (date TEXT, metric TEXT, value REAL, "
              "PRIMARY KEY (date, metric))")
    yield c
    c.close()


class _Resp:
    """requests.Response 최소 대역 — 수집기가 쓰는 것만."""

    def __init__(self, payload=None, text="", ok=True, status=200):
        self._payload, self.text, self.ok, self.status_code = payload, text, ok, status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError(f"HTTP {self.status_code}")


# ------------------------------------------------------------------ VKOSPI
def test_vkospi_matches_exact_names_only(monkeypatch):
    """실사고 회귀: 느슨한 '변동성' 포함 검색이 전략지수를 잡아 값을 오염시켰다.

    2026-07-23: '코스피 200 가치저변동성'(가격지수 ~2,600)이 VKOSPI(~15)로 저장돼
    259행이 오염, KR 매수신호(VKOSPI≥30)가 통째로 무의미해질 뻔했다.
    """
    payload = [
        {"IDX_NM": "코스피 200 변동성지수", "CLSPRC_IDX": "15.23",
         "OPNPRC_IDX": "15.00", "HGPRC_IDX": "15.80", "LWPRC_IDX": "14.90"},
        {"IDX_NM": "코스피 200 가치저변동성", "CLSPRC_IDX": "2,614.55"},   # 미끼: 전략지수
        {"IDX_NM": "코스피 200 저변동성", "CLSPRC_IDX": "1,880.10"},       # 미끼
        {"IDX_NM": "코스닥 150 변동성지수", "CLSPRC_IDX": "22.10"},
    ]
    monkeypatch.setattr(vkospi, "_fetch", lambda key, d: payload)
    rows = vkospi._rows_for("k", date(2026, 7, 23))

    assert {r[0] for r in rows} == {"VKOSPI", "VKOSDAQ"}       # 미끼 2건 제외
    vk = next(r for r in rows if r[0] == "VKOSPI")
    assert vk[6] == 15.23 and vk[3] == 15.00 and vk[4] == 15.80
    assert all(r[6] < 100 for r in rows)                       # 변동성지수는 세 자리 미만


def test_vkospi_missing_ohlc_falls_back_to_close(monkeypatch):
    """시가·고가·저가 미제공일에도 종가로 채워 캔들이 깨지지 않아야 한다."""
    monkeypatch.setattr(vkospi, "_fetch", lambda key, d: [
        {"IDX_NM": "코스피 200 변동성지수", "CLSPRC_IDX": "31.5"}])
    (row,) = vkospi._rows_for("k", date(2026, 7, 23))
    assert row[3] == row[4] == row[5] == row[6] == 31.5


def test_vkospi_skips_rows_without_close(monkeypatch):
    monkeypatch.setattr(vkospi, "_fetch", lambda key, d: [
        {"IDX_NM": "코스피 200 변동성지수", "CLSPRC_IDX": "-"}])
    assert vkospi._rows_for("k", date(2026, 7, 23)) == []


# ------------------------------------------------------------------ FRED 기준금리
def test_fed_skips_placeholder_values(con, monkeypatch):
    """FRED는 결측을 '.'으로 준다 — float() 터뜨리지 말고 건너뛸 것."""
    csv = ("observation_date,DFEDTARU\n"
           "2019-12-31,1.75\n"      # start 이전 → 제외
           "2026-01-02,.\n"         # 결측 → 제외
           "2026-01-03,4.50\n"
           "2026-01-06,4.50\n")
    monkeypatch.setattr(fed.requests, "get", lambda *a, **k: _Resp(text=csv))
    assert fed.collect(con, start="2020-01-01") == 2
    rows = con.execute("SELECT date, close FROM prices_daily ORDER BY date").fetchall()
    assert [r["date"] for r in rows] == ["2026-01-03", "2026-01-06"]
    assert rows[0]["close"] == 4.50


# ------------------------------------------------------------------ 경제 캘린더
def test_econ_calendar_major_flag_and_country_filter(con, monkeypatch):
    """major 키워드 판정과 미지원 국가 제외.

    2026-07-29 확장: US·KR → +JP·CN·EU·DE·UK (BOJ·ECB·BOE 금리결정이 빠져 있었다).
    브라질 등 그 밖은 여전히 제외.
    """
    def fake_get(url, params=None, **k):
        if params["date"] != date.today().isoformat():
            return _Resp({"data": {"rows": []}})
        return _Resp({"data": {"rows": [
            {"country": "United States", "eventName": "CPI (MoM)", "gmt": "13:30",
             "consensus": "0.2%", "previous": "0.3%"},
            {"country": "United States", "eventName": "Baltic Dry Index"},   # major 아님
            {"country": "South Korea", "eventName": "Interest Rate Decision"},
            {"country": "Brazil", "eventName": "GDP"},                       # 미지원 국가
        ]}})
    monkeypatch.setattr(econ_calendar.requests, "get", fake_get)
    monkeypatch.setattr(econ_calendar.time, "sleep", lambda s: None)

    assert econ_calendar.collect(con, days=1) == 3
    rows = con.execute("SELECT country, event, major FROM econ_calendar ORDER BY event").fetchall()
    assert {r["country"] for r in rows} == {"US", "KR"}          # Brazil 제외
    flags = {r["event"]: r["major"] for r in rows}
    assert flags["CPI (MoM)"] == 1 and flags["Interest Rate Decision"] == 1
    assert flags["Baltic Dry Index"] == 0


# ------------------------------------------------------------------ 실적 캘린더
def test_earnings_universe_filter_and_past_retention(con, monkeypatch):
    """S&P500 유니버스만 저장 + 과거분은 30일 보존(주간·월간 뷰의 '지난주' 이동용)."""
    con.execute("INSERT INTO sector_map(stock_code, market) VALUES ('AAPL','US_STOCK')")
    today = date.today()
    old = (today - timedelta(days=45)).isoformat()
    keep = (today - timedelta(days=5)).isoformat()
    con.execute("CREATE TABLE IF NOT EXISTS earnings_calendar (symbol TEXT NOT NULL, "
                "date TEXT NOT NULL, when_time TEXT, name TEXT, eps_forecast TEXT, "
                "PRIMARY KEY (symbol, date))")
    con.executemany("INSERT INTO earnings_calendar(symbol,date) VALUES (?,?)",
                    [("OLD", old), ("KEEP", keep)])

    def fake_get(url, params=None, **k):
        return _Resp({"data": {"rows": [
            {"symbol": "AAPL", "time": "time-after-hours", "name": "Apple Inc.",
             "epsForecast": "$2.10"},
            {"symbol": "PENNY", "time": "time-pre-market", "name": "Not in index"},
        ]}})
    monkeypatch.setattr(earnings.requests, "get", fake_get)
    monkeypatch.setattr(earnings.time, "sleep", lambda s: None)

    earnings.collect(con, days=3)
    syms = {r["symbol"] for r in con.execute("SELECT symbol FROM earnings_calendar")}
    assert "AAPL" in syms and "PENNY" not in syms        # 유니버스 밖 제외
    assert "KEEP" in syms and "OLD" not in syms          # 30일 보존 / 그 이전 정리


# ------------------------------------------------------------------ 내부자 거래 (Form 4)
FORM4 = b"""<?xml version="1.0"?>
<ownershipDocument>
  <periodOfReport>2026-07-20</periodOfReport>
  <reportingOwner>
    <reportingOwnerId><rptOwnerName>COOK TIMOTHY D</rptOwnerName></reportingOwnerId>
    <reportingOwnerRelationship><isDirector>0</isDirector><isOfficer>1</isOfficer>
      <officerTitle>CEO</officerTitle></reportingOwnerRelationship>
  </reportingOwner>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <transactionDate><value>2026-07-18</value></transactionDate>
      <transactionCoding><transactionCode>P</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>1000</value></transactionShares>
        <transactionPricePerShare><value>210.5</value></transactionPricePerShare>
      </transactionAmounts>
      <postTransactionAmounts>
        <sharesOwnedFollowingTransaction><value>50000</value></sharesOwnedFollowingTransaction>
      </postTransactionAmounts>
    </nonDerivativeTransaction>
    <nonDerivativeTransaction>
      <transactionCoding><transactionCode>M</transactionCode></transactionCoding>
      <transactionAmounts><transactionShares><value>999</value></transactionShares></transactionAmounts>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
</ownershipDocument>"""


def test_insider_form4_parse():
    """P/S(공개시장 매수·매도)만 채택 — M(옵션행사) 등 비시장 거래는 신호가 아니다."""
    rows = insider._parse_form4(FORM4, "AAPL", "acc-1")
    assert len(rows) == 1                                  # M 코드 제외
    (acc, sym, filed, tdate, name, title, code, shares, price, value, own) = rows[0]
    assert (sym, code, shares, price) == ("AAPL", "P", 1000.0, 210.5)
    assert value == 1000.0 * 210.5                         # 금액 = 수량 × 단가
    assert tdate == "2026-07-18" and filed == "2026-07-20"
    assert "임원" in title and name == "COOK TIMOTHY D"


def test_insider_form4_malformed_returns_empty():
    assert insider._parse_form4(b"not xml", "AAPL", "acc") == []


# ------------------------------------------------------------------ 심리지표
def test_sentiment_fear_greed_parse(monkeypatch):
    monkeypatch.setattr(sentiment.requests, "get", lambda *a, **k: _Resp(
        {"fear_and_greed": {"timestamp": "2026-07-27T00:00:00+00:00", "score": 71.4213}}))
    assert sentiment._fear_greed() == [("2026-07-27", "fear_greed", 71.4)]


def test_sentiment_survives_dead_source(con, monkeypatch):
    """비공식 소스(F&G)가 죽어도 나머지 지표는 계속 수집돼야 한다 (우아한 성능저하)."""
    con.execute("INSERT INTO prices_daily(symbol,date,close) VALUES ('^VIX','2026-07-27',17.2)")

    def boom(*a, **k):
        raise RuntimeError("endpoint gone")
    monkeypatch.setattr(sentiment.requests, "get", boom)

    assert sentiment.collect(con) == 1                      # VIX 1건은 살아남음
    r = con.execute("SELECT metric, value FROM sentiment_daily").fetchone()
    assert r["metric"] == "vix" and r["value"] == 17.2
