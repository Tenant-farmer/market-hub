# market-hub

빅픽처 마켓 애그리게이터 — 섹터 자금흐름(KR+US), 주도주, 과열 지표, 구루 13F를 한 화면에.
이후 단계: TV 웹훅 → Alpaca 페이퍼 → 키움 모의 → 실전 자동매매.

## 셋업

```
py -3.12 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env                     # KRX 계정, EDGAR User-Agent 기입
.venv\Scripts\python scripts\init_db.py

# 수집 (US는 계정 불필요, KR은 KRX 무료 계정 필요)
.venv\Scripts\python collect.py --us --backfill 730
.venv\Scripts\python collect.py --us-stocks --backfill 420
.venv\Scripts\python collect.py --gurus --sentiment
.venv\Scripts\python collect.py --kr --kr-flows --kr-map   # KRX 계정 후

# 분석 + 대시보드
.venv\Scripts\python analyze.py --us
run_dashboard.bat                          # → http://localhost:5000
```

테스트: `.venv\Scripts\python -m pytest tests -q`

## 아키텍처

```
데이터 흐름:  collectors ──▶ SQLite(data/market.db) ──▶ analytics ──▶ dashboard
              (원천별 수집)   (원본+지표 저장)          (순수 계산)     (Flask 렌더)
```

| 계층 | 위치 | 규칙 |
|---|---|---|
| 엔트리 | `collect.py` `analyze.py` `app.py` | CLI 3개. 스케줄러가 부르는 지점 |
| 수집 | `src/collectors/` | 수집기 1소스 1모듈. `base.run_collector`가 실패 격리 + `collector_runs` 기록. 공용부: `yf_util`(야후 배치), `krx_util`(KRX 로그인 게이트) |
| 분석 | `src/analytics/` | 순수 계산(pandas). 저장은 전부 `store.replace_metrics`(재계산 시 스테일 행 방지), 조회는 `store.pivot_latest`. 시세 로드는 `data.load_field`(불완전한 최신일 자동 절단) |
| 화면 | `src/dashboard/` | 앱 팩토리 + `pages/` 블루프린트(us·leaders·gurus·health) + 공용 `queries`/`fmt` |
| 테스트 | `tests/` | 핵심 로직(사분면, 백분위, 단위 보정, 피벗 저장) |

### 데이터 소스

| 소스 | 용도 | 비고 |
|---|---|---|
| yfinance | US 섹터 ETF + S&P500 종목 EOD | 비공식. 최신 1~2일 확정 지연 → 다음 수집에서 자동 보충 |
| Wikipedia | S&P500 구성종목/GICS 섹터 | |
| SEC EDGAR | 구루 13F (공식 API) | 10 req/s 제한, User-Agent 필수, PUT/CALL 구분, 천달러 단위 제출자 보정 |
| CNN F&G / Cboe | 심리지표 | 비공식 — 실패해도 다른 수집 영향 없음 |
| tradingview-screener | 시가총액 | 비공식 — 실패 허용 |
| pykrx | KR 업종지수/수급 | KRX Data Marketplace 무료 계정 필요 (`.env`) |

### 분석 지표

- **RS**: 1주/1개월/3개월 수익률의 벤치마크(SPY/KOSPI) 대비 초과수익
- **RRG 사분면**: RS-ratio(63일) × RS-momentum(21일) → 주도/약화/침체/개선
- **Leading 체류일**: 이벤트 스터디 결과 21거래일 미만 '반짝' 진입은 이후 성과 마이너스(승률 39%),
  21일 이상 유지가 지속 신호(승률 60%) — 대시보드 "체류" 컬럼의 근거
- **주도점수**: 섹터 = 3M RS 40% + 1M RS 35% + 모멘텀 25% / 종목 = 시장RS + 섹터RS + 거래량급증 (백분위 가중합)
- **과열**: RSI≥75 또는 200MA 이격 +15%

## 로드맵

- [x] US 파이프라인 (섹터·종목·심리·구루) + 대시보드
- [ ] KR 섹터/수급 (KRX 계정 대기 — 코드 완성)
- [ ] 종합 개요 페이지, KR 주도주
- [ ] 라이브 레이어 (키움 WS + Alpaca IEX WS)
- [ ] TV 웹훅 → Alpaca 페이퍼 → 키움 모의 → 실전 (risk gate 필수)
