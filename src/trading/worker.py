"""주문 엔진 상시 워커 — 백업 스위퍼 + 주기 작업(청산/신호진입/체결동기화).

웹훅 신호는 수신기가 적재 직후 스레드로 **즉시 처리**(~1초), 이 워커의 폴링은 그 백업 스위퍼.
청산·신호진입이 emit한 신호도 같은 사이클에서 즉시 처리(다음 폴 안 기다림 — 매도 시차 제거).
PC: 작업 스케줄러 ONLOGON(run_engine.bat) 상시 가동. VPS: systemd 서비스.

- ENGINE_POLL_SEC      (기본 15): 백업 폴링 간격
- EXIT_CHECK_SEC       (기본 60): 청산 규칙 평가 주기 — 손절은 지연이 돈이라 1분
- ENGINE_HEARTBEAT_SEC (기본 900): 아무 일 없어도 살아있음 기록 주기 → /health에서 확인
- process_once 예외는 잡아 기록하고 루프 유지 (브로커 일시 장애에 워커가 죽지 않음)

실행: python -m src.trading.worker   (Ctrl+C로 정지)
"""
import os
import time
import traceback
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

# 독립 실행(스케줄러/systemd) 시 .env 로드 — cwd 무관 절대경로 (Alpaca 키 등)
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from src import db
from src.trading import engine

POLL = int(os.getenv("ENGINE_POLL_SEC", "15"))
HEARTBEAT = int(os.getenv("ENGINE_HEARTBEAT_SEC", "900"))
LOG_PATH = db.ROOT / "data" / "engine.log"


def _record(status: str, rows: int, msg: str) -> None:
    con = db.connect()
    con.execute(
        "INSERT INTO collector_runs (collector, run_at, status, rows, message) VALUES (?,?,?,?,?)",
        ("engine", datetime.now().isoformat(timespec="seconds"), status, rows, msg),
    )
    con.commit()
    con.close()


def _log(msg: str) -> None:
    line = f"[engine worker] {datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    print(line, flush=True)                       # VPS(systemd)용 stdout
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:   # Windows pythonw(창없음)용 파일
            f.write(line + "\n")
    except Exception:
        pass


EXIT_CHECK = int(os.getenv("EXIT_CHECK_SEC", "60"))     # 손절은 지연이 돈 → 1분 감지
ENTRY_CHECK = int(os.getenv("SIGNAL_ENTRY_CHECK_SEC", "3600"))
RECONCILE = int(os.getenv("RECONCILE_SEC", "300"))
WATCH = int(os.getenv("WATCHDOG_CHECK_SEC", "1800"))    # 상호 감시(hourly 생존) 주기
ROT_CHECK = int(os.getenv("ROTATION_CHECK_SEC", "21600"))  # 로테이션 점검(모듈이 주1회 자체 게이트)


def main() -> None:
    _log(f"start - poll {POLL}s, heartbeat {HEARTBEAT}s, exit {EXIT_CHECK}s, "
         f"entry {ENTRY_CHECK}s, reconcile {RECONCILE}s")
    _record("ok", 0, "worker 시작")
    last_beat = time.time()
    last_exit = last_entry = last_recon = last_watch = last_rot = 0.0
    while True:
        try:
            res = engine.process_once()
            if res["processed"] or res["rejected"]:
                _record("ok", res["processed"],
                        f"처리 {res['processed']} / 거부 {res['rejected']}")
                _log(str(res))
                last_beat = time.time()
            elif time.time() - last_beat >= HEARTBEAT:
                _record("ok", 0, "heartbeat (대기 중)")
                last_beat = time.time()
            # 청산 레이어 — EXIT_ENABLED=1 일 때만, EXIT_CHECK 주기
            if os.getenv("EXIT_ENABLED") == "1" and time.time() - last_exit >= EXIT_CHECK:
                from src.trading import exits

                trig = exits.check_exits()
                if trig:
                    _record("ok", len(trig), "청산 신호: " + ", ".join(
                        f"{t['code']} {t['reason']}" for t in trig))
                    _log(f"청산 신호 {len(trig)}건: {[t['reason'] for t in trig]}")
                    _log(f"청산 즉시 처리: {engine.process_once()}")   # 매도는 다음 폴 안 기다림
                last_exit = time.time()
            # 신호진입 — SIGNAL_ENTRY_ENABLED=1 일 때만 (green→지수 매수), 하루 1회 멱등
            if os.getenv("SIGNAL_ENTRY_ENABLED") == "1" and time.time() - last_entry >= ENTRY_CHECK:
                from src.trading import signal_entry

                e = signal_entry.check_entry()
                if e:
                    _record("ok", 1, f"신호진입: {e['symbol']} ({e['signal']})")
                    _log(f"신호진입 emit: {e}")
                    _log(f"진입 즉시 처리: {engine.process_once()}")
                last_entry = time.time()
            # 체결 상태 동기화 — 주문 안 냄(안전), 항상 RECONCILE 주기
            if time.time() - last_recon >= RECONCILE:
                from src.trading import reconcile

                up = reconcile.reconcile()
                if up:
                    _record("ok", len(up), "체결반영: " + ", ".join(
                        f"{u['coid'][:16]} {u['from']}->{u['to']}" for u in up))
                    _log(f"reconcile {len(up)}건")
                last_recon = time.time()
            # 주도주 로테이션 — ROTATION_ENABLED=1 일 때만 (모듈이 ISO주당 1회 자체 게이트)
            if os.getenv("ROTATION_ENABLED") == "1" and time.time() - last_rot >= ROT_CHECK:
                from src.trading import leader_rotation

                res = leader_rotation.evaluate()
                if res and (res.get("enters") or res.get("exits")):
                    _record("ok", len(res["enters"]) + len(res["exits"]),
                            f"로테이션 {res['week']}: 진입 {len(res['enters'])} "
                            f"이탈 {len(res['exits'])}")
                    _log(f"로테이션 {res['week']}: +{[e['symbol'] for e in res['enters']]} "
                         f"-{[e['symbol'] for e in res['exits']]}")
                    _log(f"로테이션 즉시 처리: {engine.process_once()}")
                last_rot = time.time()
            # 상호 감시 — 시간별 수집 정체 시 텔레그램 경보 (30분 주기)
            if time.time() - last_watch >= WATCH:
                from src.jobs import watchdog

                con_w = db.connect()
                if watchdog.check_hourly(con_w):
                    _log("워치독: hourly 정체 경보 발송")
                con_w.close()
                last_watch = time.time()
        except Exception:
            tb = traceback.format_exc(limit=3)
            _record("error", 0, tb)
            _log("ERROR\n" + tb)
            last_beat = time.time()
        time.sleep(POLL)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("[engine worker] stopped")
