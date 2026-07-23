# 데이터 소스 카탈로그

연동된 API/사이트별로 **지금 가져오는 것**과 **그 소스가 추가로 제공할 수 있는 기능**을 구분해 기록.
(작성 2026-07-23 · 새 소스 연동 시 여기에 추가)

## 요약표

| # | 소스 | 인증 | 현재 용도 | 주기 |
|---|---|---|---|---|
| 1 | Yahoo Finance (yfinance) | 무키 | US 섹터/종목/매크로/뉴스/어닝, KR 백테스트 | 매시~일 1회 |
| 2 | KRX 정보데이터시스템 (pykrx) | KRX_ID/PW | KR 업종지수·종목·수급 | KR 장중 매시 |
| 3 | KRX Open API | KRX_OPENAPI_KEY | VKOSPI (파생상품지수) | 일 1회 |
| 4 | 한국은행 ECOS | ECOS_API_KEY | 기준금리·국고채·CPI | 일 1회 |
| 5 | DART 전자공시 | DART_API_KEY | 보유+로테이션 종목 공시 | 매시 |
| 6 | SEC EDGAR | UA 헤더만 | 구루 13F 보유내역 | 일 1회 |
| 7 | CNN Fear & Greed | 무키(비공식) | 공포탐욕지수 | 매시 |
| 8 | Cboe | 무키 | 풋콜비율 | 매시 |
| 9 | 네이버 검색 API | NAVER_CLIENT_ID | KR 뉴스 (동적 감시) | 매시 |
| 10 | FRED | 무키 | 연방기금 목표금리 | 일 1회 |
| 11 | Nasdaq 공개 API | 무키 | 실적·경제지표 캘린더 | 일 1회 |
| 12 | 키움 REST (모의) | KIWOOM_APP_KEY | KR 주문·체결·잔고 | 이벤트+주기 |
| 13 | Alpaca (페이퍼) | ALPACA_API_KEY | US 주문·체결·잔고 | 이벤트+주기 |
| 14 | Telegram Bot | BOT_TOKEN | 브리핑·경보 발신 | 아침 1회+수시 |

---

## 1. Yahoo Finance (yfinance) — 무키

**현재 수집** (`us_sectors` `us_stocks` `macro` `earnings` `news` `kr_capex` + 백테스트 스크립트)
- US 섹터 ETF 11종 + SPY/QQQ/SMH + ^VIX/^VVIX → `prices_daily(US_ETF/US_INDEX)`
- US 개별종목 일별 시세+시총 (S&P500 유니버스) → `prices_daily(US)`
- 매크로: WTI(CL=F)·금(GC=F)·미국채 ^TNX/^IRX/^FVX/^TYX·HYG·원달러(KRW=X)·달러인덱스(DX-Y.NYB)·BTC → `prices_daily(MACRO)`
- US 뉴스 (SPY/QQQ/AAPL 티커 뉴스) → `news`
- KR 분기 현금흐름(CapEx — .KS 종목) / KR 장기 백테스트용 .KS/.KQ 일별시세 (캐시 `data/kr_px_cache.pkl`)

**추가로 가능한 것**
- 분봉(1m~1h, 최근 30~60일) — 라이브 레이어 전 임시 인트라데이
- 재무제표 3종(손익/재무상태/현금흐름, 연간·분기), 애널리스트 목표가/추천 등급
- 옵션 체인(만기·행사가·IV), 배당/분할 이력, 기관·내부자 보유율
- 프리/애프터마켓 시세, 전세계 지수·환율·원자재 (사실상 전 종목)
- ⚠ 비공식 래퍼라 야후 개편 시 파손 리스크 — 핵심 의존은 EOD 시세로 한정 중

## 2. KRX 정보데이터시스템 (pykrx) — KRX_ID/PW 로그인

**현재 수집** (`kr_sectors` `kr_stocks` `kr_flows`)
- KR 업종지수 OHLCV + 지수 구성종목 매핑(주 1회) → `prices_daily(KR_INDEX)` `sector_map`
- KR 개별종목 일별 시세+시총 → `prices_daily(KR)`
- 투자자별 매매동향 (시장/종목별 외국인·기관·개인 순매수) → `investor_flows`

**추가로 가능한 것**
- 공매도 잔고·거래량, 대차잔고 — 과열/수급 보조지표 후보
- 지수·종목별 PER/PBR/배당수익률 — 밸류에이션 레이어
- 외국인 보유율(종목별), ETF/ETN/ELW 시세, 신규상장·관리종목 목록
- ⚠ 2025-12부터 로그인 의무 — 세션 1시간, 과요청 시 IP 차단 위험 (청크당 딜레이 적용 중)

## 3. KRX Open API (openapi.krx.co.kr) — KRX_OPENAPI_KEY

**현재 수집** (`vkospi`)
- 파생상품지수 시세정보(`idx/drvprod_dd_trd`) 중 **코스피 200 변동성지수** → `prices_daily(KR_INDEX, 'VKOSPI')` — 2010-01-04~ 전체 백필 완료 (4,074행)

**승인됐지만 아직 미사용** (같은 키로 즉시 호출 가능)
- KRX/KOSPI/KOSDAQ 시리즈 지수, 유가증권·코스닥 일별매매+종목기본정보, ETF/ETN/ELW 일별매매, 선물 일별매매 — **pykrx(로그인 세션)의 정식 백업 경로**. pykrx가 깨지면 여기로 전환
- 파생상품지수 응답에는 코스피200 선물지수·섹터 선물지수·커버드콜/양매도 전략지수 등 320개 포함

**추가 신청으로 가능한 것**
- 채권 3종(국채전문유통 등), 주식옵션 일별, 금/석유/배출권 시장, ESG 지수
- ⚠ 일자별(basDd) 조회 방식이라 과거 백필은 영업일당 1요청 필요

## 4. 한국은행 ECOS — ECOS_API_KEY

**현재 수집** (`ecos`)
- 한은 기준금리(722Y001, 일), 국고채 3년/10년(817Y002, 일), CPI 총지수(901Y009, 월) → `prices_daily(MACRO, 'ECOS:*')`
- 노출: 개요 매크로 카드(KR 국고3Y·10Y-3Y 스프레드) + 브리핑 🏦 줄(기준금리·CPI 전년비)

**추가로 가능한 것** (통계표 834개 확인됨)
- 802Y001 주식시장(일): KOSPI/KOSDAQ 지수·거래대금·**외국인 순매수** — pykrx 수급의 교차검증용
- M2 통화량, 가계신용(부채), 예금은행 여수신 금리
- 경기실사지수(BSI)·소비자심리지수(CSI)·뉴스심리지수 — KR 심리 레이어 후보
- 수출입(통관), GDP, 환율 전종 (원/달러 일별 등)
- ⚠ 변동성지수는 없음 (834개 전수 스캔으로 확인)

## 5. DART 전자공시 — DART_API_KEY

**현재 수집** (`dart`)
- 감시 종목(고정 보유 2 + rotation_slots KR 동적)의 최근 7일 공시 목록(list.json) → `news(source='DART')` — 개요 카드·브리핑에 보장 슬롯 2건
- corpCode.xml 1회 다운로드 → `dart_corp` (상장사 3,978 매핑)

**추가로 가능한 것**
- 상장사 재무제표 API (단일/다중회사, XBRL 원본) — 분기 실적 자동 수집
- 배당·자사주 취득/처분·유상증자·CB/BW 발행 **개별 상세 API** (사업보고서 주요정보)
- 대량보유(5%) 및 임원·주요주주 지분변동 — 수급 이벤트 감지
- 공시 원문 문서(document.xml) — 키워드 알림(예: '유상증자' 뜨면 텔레그램)
- 기업개황(업종·결산월·주소)

## 6. SEC EDGAR — EDGAR_USER_AGENT 헤더만

**현재 수집** (`gurus`)
- 구루 매니저 13F-HR 분기 보유내역 → `guru_filings/holdings/changes`, QoQ diff → /gurus 페이지

**추가로 가능한 것**
- companyfacts XBRL: 전 상장사 표준화 재무 시계열 (매출·이익·현금흐름) — 무료 미국 재무 DB
- Form 4 인사이더 매매 (실시간에 가까움) — 내부자 매수 시그널
- 13D/G (5% 대량보유), 8-K (중요 이벤트), 10-K/Q 원문
- full-text search API — 키워드 기반 공시 감시
- ⚠ 10 req/s 제한, 13F는 최대 45일 지연 공시

## 7. CNN Fear & Greed — 무키 (비공식)

**현재 수집** (`sentiment`)
- 종합 F&G 값 + 등급 → `sentiment_daily` — 신호등 avoid_greed(≥75) 판정에 사용

**추가로 가능한 것**
- 같은 엔드포인트에 **과거 시계열** + 7개 구성요소(모멘텀·풋콜·정크본드 수요 등) 포함 — 구성요소별 분해 분석 가능
- ⚠ 비공식 — 언제든 파손 가능 (개별 실패 허용 설계, 죽으면 패널 숨김)

## 8. Cboe — 무키 (공식 무료)

**현재 수집** (`sentiment`)
- 전체 풋콜비율 (일별 페이지 파싱) → `sentiment_daily`

**추가로 가능한 것**
- 상품별 풋콜 (SPX만/ETP만/VIX 옵션) — 기관(SPX) vs 개인(ETP) 분리
- VIX 선물 기간구조 CSV — 콘탱고/백워데이션 (공포 정점 감지 보조)
- SKEW 지수 (테일리스크 프라이싱)

## 9. 네이버 검색 API — NAVER_CLIENT_ID/SECRET (2026-07-23 연동)

**현재 수집** (`news`)
- KR 뉴스 검색: 코스피 + 고정 보유 + **로테이션 슬롯 동적** (13검색어, dart_corp 이름 매핑)
  → `news(source='NAVER')` — 분 단위 속보성, 원문 링크(originallink) 우선
- 키가 없으면 Google News RSS 고정 키워드로 자동 폴백

**추가로 가능한 것**
- 블로그/카페/쇼핑 등 다른 검색 버티컬, 데이터랩(검색 트렌드 — 종목 관심도 프록시)
- 일 25,000회 무료 — 현재 사용량(시간당 13회)의 여유 큼

## 10. FRED — 무키

**현재 수집** (`fed`)
- 연방기금 목표금리 상한(DFEDTARU) CSV → `prices_daily(MACRO)` — /fed 페이지·FOMC 워치

**추가로 가능한 것**
- 무키 CSV로 대부분의 미국 거시 시계열: 실업률, CPI/PCE, 국채 전만기, M2, 하이일드 스프레드(진짜 OAS), 리세션 지표 등 82만 시리즈

## 11. Nasdaq 공개 API — 무키

**현재 수집** (`earnings` `econ_calendar`)
- 실적 캘린더 (S&P500 유니버스, 향후 N일) → `earnings_calendar`
- 경제지표 캘린더 (미국·한국 이벤트) → `econ_calendar` — 브리핑 📅 섹션

**추가로 가능한 것**
- 배당 캘린더, IPO 캘린더, 애널리스트 추천 변경
- ⚠ 비공식 성격 (공개 웹 API) — 파손 허용 설계

## 12. 키움 REST API (모의 mockapi) — KIWOOM_APP_KEY/SECRET

**현재 사용** (`brokers/kiwoom.py`)
- 토큰 발급(au10001), 매수/매도 주문(kt10000/10001), 주문 취소(kt10003)
- 미체결/체결 조회(kt00007) — 체결확인 루프·유령주문 검증·reconcile
- 계좌평가잔고(kt00018) — /positions + 에쿼티 스냅샷
- 레이트리밋(1700) 방어: 1초 쓰로틀 + 검증 후 재시도

**추가로 가능한 것**
- **실시간 웹소켓** (체결가·호가·주문체결 통보, ~97심볼) — Phase 5 라이브 레이어 핵심
- 일/분봉 차트 조회 (ka10081 계열) — 야후 .KS 대체 가능
- 조건검색식 실행, 일별 실현손익, 예수금 상세, 신용 주문
- 실전 전환: 도메인만 교체 (실전 앱키 발급 필요 — 사용자 숙제)

## 13. Alpaca (페이퍼) — ALPACA_API_KEY/SECRET

**현재 사용** (`brokers/alpaca.py`)
- 계좌/포지션 조회, 시장가 주문(소수점 수량), 주문 상태 조회 — reconcile·/positions·스냅샷

**추가로 가능한 것**
- **IEX 실시간 웹소켓 무료** — US 라이브 레이어
- 과거 분봉/일봉 bars API, Benzinga 뉴스 API
- **브라켓/OCO 주문** — 손절 -8%를 서버사이드 스탑으로 이관 가능 (워커 다운 시에도 보호)
- 시장 캘린더/시계 API — 휴장일 정확 판정 (현재 로컬시간 근사)

## 14. Telegram Bot API — TELEGRAM_BOT_TOKEN/CHAT_ID

**현재 사용** (`briefing` `watchdog` `reconcile`)
- 아침 브리핑(시세판 포맷), 워커 정체 경보, 매도 워치독 경보 — 발신 전용

**추가로 가능한 것**
- **양방향 명령** (getUpdates/webhook): `/잔고` `/킬스위치` `/신호` 원격 조작
- 이미지 전송 (에쿼티 곡선 차트 첨부), 인라인 버튼 (승인/거부 등 대화형)

---

## 미연동 (키 보유 또는 후보)

| 소스 | 상태 | 용도 후보 |
|---|---|---|
| KOFIA FREESIS | 키 신청 필요 | 고객예탁금·신용잔고 (시중 대기자금) |
| 키움 실전 API | 앱키 신청 필요 | 실전 전환 (2주 검증 통과 후) |
