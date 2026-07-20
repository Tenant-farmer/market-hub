-- market-hub SQLite 스키마
-- 날짜는 전부 ISO 문자열(YYYY-MM-DD), 금액은 원/달러 원단위

CREATE TABLE IF NOT EXISTS prices_daily (
    symbol  TEXT NOT NULL,          -- US 티커 / KR 종목코드(6자리) / KR 업종지수코드
    market  TEXT NOT NULL,          -- US | KR | KR_INDEX | US_INDEX
    date    TEXT NOT NULL,
    open    REAL, high REAL, low REAL, close REAL,
    volume  REAL,
    value   REAL,                   -- 거래대금 (KR 제공값, US는 close*volume 근사)
    PRIMARY KEY (symbol, date)
);
CREATE INDEX IF NOT EXISTS idx_prices_market_date ON prices_daily (market, date);

CREATE TABLE IF NOT EXISTS sector_map (
    stock_code  TEXT PRIMARY KEY,
    market      TEXT NOT NULL,
    sector_code TEXT NOT NULL,
    sector_name TEXT,
    name        TEXT,               -- 종목명
    as_of       TEXT                -- 구성종목 스냅샷 기준일
);

CREATE TABLE IF NOT EXISTS investor_flows (
    scope      TEXT NOT NULL,       -- market | sector | stock
    code       TEXT NOT NULL,       -- KOSPI/KOSDAQ | 업종코드 | 종목코드
    date       TEXT NOT NULL,
    investor   TEXT NOT NULL,       -- foreign | institution | individual
    net_value  REAL,                -- 순매수 대금(원)
    net_volume REAL,
    PRIMARY KEY (scope, code, date, investor)
);
CREATE INDEX IF NOT EXISTS idx_flows_date ON investor_flows (date);

-- long-form: 지표 추가 시 스키마 변경 없음
CREATE TABLE IF NOT EXISTS analytics_daily (
    date   TEXT NOT NULL,
    scope  TEXT NOT NULL,           -- us_sector | kr_sector | stock
    code   TEXT NOT NULL,
    metric TEXT NOT NULL,           -- rs_ratio | rs_mom | quadrant | ret_5 | leader_score | overheat_rsi ...
    value  REAL,
    PRIMARY KEY (date, scope, code, metric)
);

CREATE TABLE IF NOT EXISTS sentiment_daily (
    date   TEXT NOT NULL,
    metric TEXT NOT NULL,           -- fear_greed | vix | equity_pc_ratio
    value  REAL,
    PRIMARY KEY (date, metric)
);

CREATE TABLE IF NOT EXISTS guru_filings (
    accession    TEXT PRIMARY KEY,  -- EDGAR accession number
    cik          TEXT NOT NULL,
    manager_name TEXT,
    quarter      TEXT,              -- 2026Q1
    filed_date   TEXT,
    is_amendment INTEGER DEFAULT 0  -- 13F-HR/A
);

CREATE TABLE IF NOT EXISTS guru_holdings (
    accession TEXT NOT NULL,
    cusip     TEXT NOT NULL,
    ticker    TEXT,                 -- CUSIP→티커 매핑 후 채움 (없으면 NULL)
    name      TEXT,
    shares    REAL,
    value_usd REAL,
    pct       REAL,                 -- 포트폴리오 내 비중
    PRIMARY KEY (accession, cusip)
);

CREATE TABLE IF NOT EXISTS guru_changes (
    cik          TEXT NOT NULL,
    quarter      TEXT NOT NULL,
    cusip        TEXT NOT NULL,
    ticker       TEXT,
    name         TEXT,              -- 발행사명 (CUSIP→티커 매핑 전 표시용)
    action       TEXT,              -- new | add | trim | exit
    delta_shares REAL,
    delta_value  REAL,
    PRIMARY KEY (cik, quarter, cusip)
);

CREATE TABLE IF NOT EXISTS stock_meta (
    symbol    TEXT PRIMARY KEY,
    mcap      REAL,                 -- 시가총액 (USD/KRW)
    tv_symbol TEXT,                 -- 트레이딩뷰 심볼 (예: NASDAQ:AAPL) — 위젯 임베드용
    as_of     TEXT
);

CREATE TABLE IF NOT EXISTS earnings_calendar (
    symbol       TEXT NOT NULL,
    date         TEXT NOT NULL,     -- 발표일 (US 기준)
    when_time    TEXT,              -- time-pre-market | time-after-hours | time-not-supplied
    name         TEXT,
    eps_forecast TEXT,
    PRIMARY KEY (symbol, date)
);

CREATE TABLE IF NOT EXISTS econ_calendar (
    date      TEXT NOT NULL,        -- 발표일 (현지 기준)
    gmt       TEXT,                 -- GMT HH:MM
    country   TEXT,                 -- US | KR
    event     TEXT,
    actual    TEXT,
    consensus TEXT,
    previous  TEXT,
    major     INTEGER DEFAULT 0     -- 주요 지표 (CPI/GDP/고용 등 키워드)
);

CREATE TABLE IF NOT EXISTS collector_runs (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    collector TEXT NOT NULL,
    run_at    TEXT NOT NULL,        -- ISO datetime
    status    TEXT NOT NULL,        -- ok | error
    rows      INTEGER DEFAULT 0,
    message   TEXT
);

-- US 섹터 CapEx (섹터 시총 상위 종목의 분기 현금흐름 스냅샷, 월 1회 갱신)
CREATE TABLE IF NOT EXISTS us_capex (
    sector     TEXT NOT NULL,       -- 한글 섹터명 (sector_map US_STOCK 기준, 금융·리츠 제외)
    symbol     TEXT NOT NULL,
    latest_q   TEXT,                -- 최신 공시 분기 말일
    capex_ttm  REAL,                -- 최근 4개 분기 합 (USD)
    q_latest   REAL,                -- 최신 분기
    q_yoy_base REAL,                -- 전년 동분기 (YoY 분모)
    fetched_at TEXT,
    PRIMARY KEY (sector, symbol)
);

-- KR 업종 CapEx (KOSPI 비금융 업종 시총 상위 5종목, 야후 .KS, 월 1회 갱신)
CREATE TABLE IF NOT EXISTS kr_capex (
    sector     TEXT NOT NULL,
    symbol     TEXT NOT NULL,       -- 6자리 종목코드
    latest_q   TEXT,
    capex_ttm  REAL,                -- 원화
    q_latest   REAL,
    q_yoy_base REAL,
    fetched_at TEXT,
    PRIMARY KEY (sector, symbol)
);
