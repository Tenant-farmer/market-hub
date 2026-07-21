# market-hub

빅픽처 마켓 애그리게이터 + 단계적 자동매매. 국내·미국 시장의 **섹터 자금흐름·주도주·과열·구루 13F·
설비투자(CapEx)·매수 신호등**을 한 화면에서 보고, 검증된 신호를 **TV 웹훅 → 엔진 → 브로커**로
자동 집행한다 (현재 페이퍼 단계).

개발 이력(이슈·결정·검증): [docs/HISTORY.md](docs/HISTORY.md) · VPS 이전/스모크: [docs/VPS.md](docs/VPS.md)

## 무엇을 보여주는가

| 페이지 | 내용 |
|---|---|
| `/` 개요 | 시장온도 카드(30일 스파크라인)·매크로 스트립·**매수 신호등**(VIX×VVIX×F&G 백테스트)·F&G 계기판·투자자 동향·KR/US 업종 수급·**자금 쏠림 + 섹터 CapEx**·상대수익 차트·일정 |
| `/us` `/kr` 섹터 | RRG 사분면 + **벤치마크 대비 상대수익 겹침 차트** + 주도점수 랭킹 |
| `/leaders` `/kr-leaders` 주도주 | 종목 단위 주도점수 랭킹 (시총 하한·업종/시장 필터) |
| `/stocks` 종목 허브 | 시장·시총구간·섹터 필터 + 종목명/코드 검색 + 시총·거래량 정렬 |
| `/stock/<티커>` 상세 | 펀더멘털·애널리스트·**종목 수급 90일**·CapEx 추이·월별 히트맵 + 차트(1시간/4시간/일/주/월, 10년) |
| `/calendar` `/fed` 일정 | 경제·실적 캘린더 + Fed Watch(FOMC D-day·금리 추이) |
| `/gurus` 구루 | SEC 13F 분기 diff·컨센서스 (버핏·애크먼·버리 등) |
| `/health` 상태 | 수집기 신선도 배지 + **자동매매 실전 게이트 상태**·최근 주문 |

## 셋업

```
py -3.12 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env                     # KRX 계정, EDGAR User-Agent 등 기입
.venv\Scripts\python scripts\init_db.py

# 수집 (US는 계정 불필요, KR은 KRX 무료 계정 필요)
.venv\Scripts\python collect.py --us --backfill 730
.venv\Scripts\python collect.py --us-stocks --backfill 420
.venv\Scripts\python collect.py --gurus --sentiment --capex
.venv\Scripts\python collect.py --kr --kr-flows --kr-map   # KRX 계정 후

# 분석 + 대시보드
.venv\Scripts\python analyze.py --us --kr
run_dashboard.bat                          # → http://localhost:5000
```

테스트: `.venv\Scripts\python -m pytest -q` (20건)

### 상시 운영 (Windows 작업 스케줄러, 로그인 시 자동)

| 작업 | 역할 |
|---|---|
| `market-hub-dashboard` | 대시보드 + 웹훅 수신기 상시 가동 (localhost:5000) |
| `market-hub-hourly` | 매시 :05 수집+분석 (시장 상태 라우팅, 로그 `data/scheduler.log`) |
| `market-hub-engine` | 주문 엔진 워커 — signals 큐 15초 폴링 (로그 `data/engine.log`) |

재시작: `schtasks /End /TN <작업>` 후 `schtasks /Run /TN <작업>`. 텔레그램 아침 브리핑·일 1회 백업 포함.

## 자동매매 파이프라인

```
TradingView 알림 ─(웹훅)▶ POST /hook/tv ─▶ signals 큐 ─▶ engine 워커
                          (시크릿 검증)      (멱등키)      │
                                                          ▼
                                       risk 게이트 + 실전 게이트(mode/armed)
                                                          │
                                          ┌───────────────┴───────────────┐
                                       paper_log (KR·기록만)         Alpaca 페이퍼 (US·크립토)
```

- **실전 게이트**: `mode`(log/paper/live) + `armed` **둘 다** 충족해야 실주문. 기본 paper·미무장(안전).
  조작: `python -m src.trading.control status | mode <m> | arm | disarm`
- **리스크 한도**: 킬스위치(`KILL_SWITCH=1`)·팻핑거·주문금액 상한(USD/KRW)·일일 건수 서킷브레이커
- **멱등**: 신호 해시 + `client_order_id`로 TV 재전송·브로커 재제출에도 중복 주문 방지
- 상태·최근 주문은 `/health`에서 확인. 키움 모의 어댑터는 준비 중(REST 앱키 대기)

## 아키텍처

```
외부 소스 ──▶ collectors ──▶ SQLite(data/market.db) ──▶ analytics ──▶ dashboard
                                     ▲                                    │
                          trading (receiver→engine) ◀──── TV 웹훅        └▶ 브라우저
```

| 계층 | 위치 | 규칙 |
|---|---|---|
| 엔트리 | `collect.py` `analyze.py` `app.py` + `src/trading/worker.py` | CLI/상시 프로세스. 스케줄러 진입점 |
| 수집 | `src/collectors/` | 1소스 1모듈. `base.run_collector`가 실패 격리 + `collector_runs` 기록. 공용부 `yf_util`·`krx_util` |
| 분석 | `src/analytics/` | 순수 계산(pandas). 저장은 `store.replace_metrics`(스테일 방지), 조회는 `store.pivot_latest`, 시세는 `data.load_field`(불완전 최신일 절단) |
| 화면 | `src/dashboard/` | 앱 팩토리 + `pages/` 11 blueprint + `queries`(+`queries_macro`·`queries_calendar`) + `fmt` + `auth`(Basic) |
| 매매 | `src/trading/` | `receiver`·`engine`·`worker`·`risk`·`state`·`control` + `brokers/`(base·paper_log·alpaca) |
| 테스트 | `tests/` | 핵심 로직 + 자동매매 배관(시크릿·멱등·게이트·리스크·인증) 20건 |

### 데이터 소스

| 소스 | 용도 | 비고 |
|---|---|---|
| yfinance | US ETF/종목·매크로·종목 상세·CapEx·인트라데이 | 비공식. 최신 1~2일 지연 자동 보충. 데이터센터 IP는 429 주의 |
| pykrx | KR 업종지수·종목·투자자 수급·공매도 | KRX Data Marketplace 무료 계정 필요(`.env`). 공식 OpenAPI는 수급·공매도 미커버 |
| SEC EDGAR | 구루 13F (공식) | 10 req/s, User-Agent 필수, PUT/CALL·단위 보정 |
| Nasdaq API | 실적·경제 캘린더 | 비공식 |
| Cboe · CNN F&G · FRED | 풋콜·공포탐욕·연방금리 | 비공식(F&G) / 공식(FRED) |
| Wikipedia · tradingview-screener | S&P500 구성·시가총액 | 비공식 — 실패 허용 |
| Alpaca (페이퍼) | 미국·크립토 주문 집행 | `.env` 키. 실계좌 아님 |
| TradingView | 웹훅 신호 | 요금제 웹훅 지원 필요 |

### 핵심 지표

- **RS / RRG**: 벤치마크 대비 초과수익 + RS-ratio(63일)×momentum(21일) 사분면
- **주도점수**: 섹터 = 3M RS·1M RS·모멘텀 / 종목 = 시장RS·섹터RS·절대수익·52주고점比·거래량 (백분위 가중합).
  폭락장 "버티기" 오판 방지 v2 (백테스트 승률 62→75%)
- **매수 신호등**: VIX×VVIX×F&G 상태 분류 (백테스트 근거, `scripts/*_backtest.py`)
- **체류일**: Leading 21거래일 미만 진입은 성과 마이너스 — "체류" 컬럼 근거
- **과열**: RSI≥75 또는 200MA 이격 +15%

## 로드맵

- [x] US·KR 파이프라인 (섹터·종목·수급·심리·구루·CapEx) + 분석 엔진
- [x] 대시보드 전 페이지 (개요·섹터·주도주·종목허브·상세·일정·Fed·구루·상태)
- [x] 자동매매 1~2단계: 웹훅 수신기 + paper_log + **Alpaca 페이퍼 실체결** + 상시 엔진 + 실전 게이트
- [x] 운영: 상시 3프로세스 + 텔레그램 브리핑 + 백업 + 대시보드 접근 보안
- [ ] 키움 모의 어댑터 (REST 앱키 대기)
- [ ] VPS 이전 ([docs/VPS.md](docs/VPS.md)) → 2주 무인 페이퍼/모의 → 실전 게이트 개방
- [ ] 라이브 레이어 (키움 WS + Alpaca IEX WS)

## 보안

- 대시보드는 `DASH_PASS` 설정 시 Basic Auth 강제(웹훅 제외). 로컬은 무인증.
  VPS 외부 노출은 `DASH_HOST=0.0.0.0` + `DASH_PASS` + HTTPS(cloudflared 터널) 경유.
- `.env`(계정·키)와 `data/`(DB·백업)는 git 제외. 시크릿은 로그·커밋에 남기지 않는다.
