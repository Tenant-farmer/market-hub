# VPS 이전 가이드

PC 상시 가동 부담을 없애기 위해 market-hub를 VPS로 옮긴다. 자동매매는 24시간 안 꺼지는 환경이
필요하므로(새벽 미국장 신호 유실 방지) VPS가 정답이다. **먼저 스모크 테스트로 데이터 소스가
살아있는지 확인한 뒤** 이전한다.

## 1. 제공사 결정

우리 워크로드는 **KRX(pykrx) 의존도가 절대적**(투자자 수급·공매도는 KRX 공식 OpenAPI도 대체 못 함)이라,
한국 리전이 유리하다.

| 후보 | 월 | 리전 | 판단 |
|---|---|---|---|
| **Vultr 서울(ICN) 1GB** | ~$6 | 🇰🇷 | **1순위** — 한국 IP(KRX 안전) + 국제 제공사(깨끗한 IP, yfinance 유리) + 시간과금(테스트 저렴) |
| AWS Lightsail 서울 1GB | ~$7 | 🇰🇷 | 대안. AWS 안정성, 월과금 |
| 국내(카페24/가비아/네이버클라우드) | ~$4~5 | 🇰🇷 | KRX 최고 안전이나 툴링 약함 |
| Contabo 싱가포르 | ~$5 | 🇸🇬 | 최저가·고사양이나 한국 리전 없음 + 저가 공유 IP라 yfinance 429 위험 → **스모크 통과 시에만** |

> 사양보다 **위치·IP 평판**이 중요하다. 우리 앱은 가벼워(SQLite+Flask+배치) 1GB면 충분하고,
> 콘타보의 고사양은 안 쓰이고 나쁜 IP 평판만 직격으로 맞는다.

**근거 요약** (조사: pykrx 이슈 #170/#244, yfinance #2422, openapi.krx.co.kr):
- KRX 차단은 "요청 과다" 기반이지 해외 IP 기반이 아님 → 우리 사용량(로그인+1초 딜레이)이면 통과 가능성 높음
- 진짜 복병은 yfinance — 2025년 rate limit 강화로 데이터센터/저가 공유 IP에서 429 잘 터짐
- 스크래핑 성공은 IP 평판에 달렸고 이는 대역·시점마다 복불복 → **스모크 실측이 유일한 판별법**

## 2. 사전 검증 — 스모크 테스트 (이전의 첫 관문)

VPS를 **월 단위로** 하나 빌려(연 결제 금물), 전체 이전 전에 데이터 소스가 되는지 실측한다.

```bash
git clone https://github.com/Tenant-farmer/market-hub.git && cd market-hub
python3 -m venv .venv && . .venv/bin/activate
pip install pykrx yfinance python-dotenv requests
export KRX_ID=아이디 KRX_PW=비밀번호        # 또는 .env
python scripts/vps_smoketest.py
```

맨 아래 판정을 본다:
- **"이전 가능 — 핵심 소스 전부 통과"** (exit 0) → 이전 진행
- **"재검토 필요 — 핵심 실패: ..."** (exit 1) → 그 IP는 부적합. 다른 리전/제공사로 재시도

검사 항목 (핵심 = 실패 시 이전 보류):
- KRX 일별시세(OHLCV)
- **KRX 투자자 수급** ★핵심 (공식 OpenAPI 미커버 — 우리 차별화)
- **KRX 공매도 잔고** ★핵심
- KRX 전체 스냅샷 (무거운 호출)
- **yfinance US(AAPL)** ★핵심 (429 여부)
- yfinance KR(.KS)
- 서버 IP/국가 (한국 리전인지 확인)

> 로컬(한국 가정 IP)에선 전 항목 PASS가 확인됨. VPS에서 FAIL이 뜨면 그건 IP 문제로 확정할 수 있다.

## 3. 이전 체크리스트 (Windows → Linux)

스모크 통과 후 진행. 코드는 pathlib·환경변수 기반이라 대부분 이식되고, **작업 스케줄러 → systemd**가
핵심 포팅이다.

**(1) 셋업**
```bash
sudo apt update && sudo apt install -y python3-venv git
git clone https://github.com/Tenant-farmer/market-hub.git ~/market-hub && cd ~/market-hub
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env && nano .env      # 모든 키 기입 (아래 참조)
.venv/bin/python scripts/init_db.py
# DB 이전: PC의 backups/*.zip 에서 market.db 를 data/ 로 복사하면 수집 이력 승계
```

**(2) `.env` (VPS 추가분)**
```
DASH_HOST=0.0.0.0            # 외부 바인드
DASH_PASS=<강한 비밀번호>     # 대시보드 인증 강제
KRX_ID= / KRX_PW=           # KRX 계정
ALPACA_API_KEY= / ALPACA_API_SECRET=
WEBHOOK_SECRET=             # TV 알림 payload 시크릿
TELEGRAM_BOT_TOKEN= / TELEGRAM_CHAT_ID=
```

**(3) systemd 서비스 3개** (`/etc/systemd/system/`)

`mh-dashboard.service` — 대시보드+웹훅:
```ini
[Unit]
Description=market-hub dashboard
After=network.target
[Service]
WorkingDirectory=/home/USER/market-hub
ExecStart=/home/USER/market-hub/.venv/bin/python app.py
Restart=always
[Install]
WantedBy=multi-user.target
```

`mh-engine.service` — 주문 엔진 워커 (같은 형식, `ExecStart=... -m src.trading.worker`).

`mh-hourly.service` + `mh-hourly.timer` — 매시 :05 수집 (oneshot + OnCalendar):
```ini
# mh-hourly.timer
[Timer]
OnCalendar=*:05
[Install]
WantedBy=timers.target
```
```bash
sudo systemctl enable --now mh-dashboard mh-engine mh-hourly.timer
journalctl -u mh-engine -f        # 로그 확인
```

**(4) 웹훅 노출 — 고정 주소**
- cloudflared 네임드 터널(도메인 ~$10/년, HTTPS 종단) 또는 ngrok 고정 도메인
- TV 알림 웹훅 URL을 `https://<도메인>/hook/tv` 로 갱신 (임시 trycloudflare 주소 아님)

## 4. 보안

- `DASH_PASS` 반드시 설정 (미설정이면 무인증 노출). 웹훅은 인증 예외이나 자체 시크릿으로 보호됨
- 외부 노출은 HTTPS(터널) 경유 — Basic Auth는 평문 HTTP에서 도청 가능
- 방화벽: 5000 포트를 직접 열지 말고 터널만 노출(`ufw`로 22 외 차단), 또는 터널이 localhost로 포워딩
- `.env`·`data/` 는 git 제외 유지. 백업 zip에 `.env`가 포함되니 백업 파일도 비공개 보관

## 5. 이전 후 검증 → 실전 경로

1. `journalctl`로 3 서비스 정상 + `/health` 접속(인증 걸림) 확인
2. 하루 지켜보며 hourly 수집·엔진 heartbeat 이력 확인
3. TV 실알림 1건으로 웹훅→엔진→paper_log/Alpaca 전 구간 재확인
4. **2주 무인 페이퍼/모의 가동** — 무사고 확인 후에만 실전 게이트 개방
   (`control.py mode live` + `arm`, risk 한도·일손실 한도 설정)

## 6. 주의

- KRX 세션 1시간 만료 — pykrx 자동 재로그인, 과도 요청 시 IP 차단 위험(청크당 1초 딜레이 유지)
- yfinance 429가 이전 후 나타나면: 캐시 TTL 상향(이미 6h)·호출 분산·최후엔 프록시
- 임시 quick 터널은 URL이 매번 바뀌어 상시 웹훅에 부적합 — 고정 주소 필수
- 로컬 PC와 VPS를 동시에 돌리지 말 것 (양쪽이 같은 TV 웹훅을 받으면 중복 — 멱등키가 막지만 혼선)
