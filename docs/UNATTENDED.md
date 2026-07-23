# 무인 가동 2주 관찰 — 2026-07-23 개시

목적: VPS 이전 전 마지막 관문. 완전 자동 루프(수집→분석→신호→게이트→체결→체결확인→청산)를
사람 개입 없이 2주 검증한다. PC 상시 ON (사용자 확약).

## 가동 상태
- **게이트**: `EXIT_ENABLED=1` (자동청산 — 손절 -8% · 주도이탈 RS<0, 추세이탈 off)
  + `SIGNAL_ENTRY_ENABLED=1` (매수신호등 green → SPY 1주/일 멱등)
- **모드**: paper · 미무장 — 실주문 물리적으로 불가 (US Alpaca 페이퍼 / KR 키움 모의)
- **안전망**: 리스크 게이트(금액·건수 상한) → 체결확인 reconcile(Alpaca+키움) → 매도 워치독(120초)
  → 상호 감시 워치독(hourly↔엔진, 정체 시 텔레그램 즉시 경보, 6h 쿨다운)

## 관찰 채널
1. 아침 텔레그램 브리핑 **⚙ 시스템 줄** — 24h 수집 ok/에러 · 주문 수 · 경보 수 · 게이트
2. **워치독 경보** — 프로세스 정체 시 즉시 (브리핑이 안 오면 PC 자체 다운 의심)
3. 대시보드 `/health` · `/positions`

## 합격 기준 (2주 후 VPS 이전 판단)
- [ ] 워치독 경보 0건 (발생 시 원인 규명·해소하면 그 시점부터 재카운트)
- [ ] 수집 에러율 < 5% (외부 소스 일시 장애는 제외하되 기록)
- [ ] 자동 주문 전건이 리스크 게이트 기록 + reconcile 체결 반영을 가짐
- [ ] 이중주문 0건 · 매도 미체결 방치 0건 (워치독 자동복구는 정상으로 인정)
- [ ] 청산/진입 판단이 규칙과 일치 (주 1회 표본 점검 → HISTORY 기록)

## 운영 절차
- **재시작(코드 반영 시)** — `schtasks /End`는 pythonw 자식을 못 죽여 **고아 워커**가 남을 수 있음
  (실측: 이중 워커 발생). 반드시 명령줄 매칭 kill 후 재기동:
  ```powershell
  Get-CimInstance Win32_Process -Filter "Name like 'pythonw%'" |
    Where-Object { $_.CommandLine -match 'src\.trading\.worker' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
  schtasks /End /TN market-hub-engine; schtasks /Run /TN market-hub-engine
  ```
  (대시보드도 동일 요령 — `app\.py` 매칭)
- **긴급 정지**: `.env`에 `KILL_SWITCH=1` + 워커 재시작 (paper·미무장이라 이미 실돈 안전)
- 게이트 해제: `.env`에서 `EXIT_ENABLED`/`SIGNAL_ENTRY_ENABLED` 제거 후 워커 재시작
