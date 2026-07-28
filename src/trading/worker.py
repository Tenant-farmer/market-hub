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
    """실행 기록. **절대 예외를 던지지 않는다** — 관측이 관측 대상을 죽이면 안 된다.

    2026-07-28 급사의 진범이 여기였다. 흐름:
      ① try 안에서 `_record("ok", …)` 하트비트 → DB 락이면 예외
      ② except가 받아 `_record("error", tb)`를 부르는데 **여전히 락이라 또 터진다**
      ③ 그 예외는 잡을 사람이 없어 while 루프 밖으로 튄다. main()도 __main__도
         KeyboardInterrupt만 잡으므로 → 프로세스 종료
    결과가 관측된 그대로다: DB 기록 0건 · 로그 0줄(_log는 _record 뒤라 도달 못 함) ·
    WER 없음(크래시가 아니라 정상 종료). 05:51 마지막 하트비트 → 06:06 다음 하트비트가
    06:05 hourly의 쓰기 락과 겹치며 죽었고, 07:05 워치독이 73분 만에 발견했다.
    """
    try:
        con = db.connect()
        con.execute(
            "INSERT INTO collector_runs (collector, run_at, status, rows, message) "
            "VALUES (?,?,?,?,?)",
            ("engine", datetime.now().isoformat(timespec="seconds"), status, rows, msg),
        )
        con.commit()
        con.close()
    except Exception as e:                 # 파일 로그는 DB와 무관하게 남는다
        _log(f"기록 실패({status}): {type(e).__name__}: {e}")


def _log(msg: str) -> None:
    line = f"[engine worker] {datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    print(line, flush=True)                       # VPS(systemd)용 stdout
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:   # Windows pythonw(창없음)용 파일
            f.write(line + "\n")
    except Exception:
        pass


# ── 급사 진단 ────────────────────────────────────────────────────────────────
# 2026-07-28: 워커가 05:51~08:29(2시간 38분) 사라졌는데 **아무 흔적이 없었다**.
# Python 예외 아님(루프가 잡아 로깅함) · 절전 아님(Kernel-Power 이벤트 없음) ·
# 크래시 아님(WER 기록 없음) · 스케줄러 종료 아님. 원인을 못 찾았으므로
# **다음번엔 잡을 수 있게** 계측한다:
#   ① 종료 훅 — 정상 종료면 'stop' 기록이 남는다. 기록 없이 사라졌으면 **급사**
#   ② 생존 파일 — 매 폴(15초)마다 시각을 덮어써 '마지막 살아있던 순간'을 초 단위로 남긴다
#      (DB 하트비트는 15분 주기라 해상도가 부족했다)
ALIVE_PATH = db.ROOT / "data" / "engine_alive.txt"


def _touch_alive() -> None:
    try:
        ALIVE_PATH.write_text(datetime.now().isoformat(timespec="seconds"), encoding="utf-8")
    except Exception:
        pass


def _on_exit(reason: str) -> None:
    """정상 종료 경로에서만 호출된다 — 이 기록의 **부재**가 급사의 증거."""
    _log(f"stop ({reason})")
    try:
        _record("stop", 0, f"worker 종료: {reason}")
    except Exception:
        pass


def _install_exit_hooks() -> None:
    import atexit
    import signal

    atexit.register(_on_exit, "정상 종료(atexit)")
    for sig in ("SIGTERM", "SIGINT", "SIGBREAK"):        # Windows는 SIGBREAK도 온다
        s = getattr(signal, sig, None)
        if s is None:
            continue
        try:
            signal.signal(s, lambda n, f: (_on_exit(f"시그널 {n}"), os._exit(0)))
        except (ValueError, OSError):
            pass                                          # 스레드 등에서 설정 불가 시 무시


def last_alive() -> str | None:
    """마지막 생존 기록 시각 (진단·워치독용)."""
    try:
        return ALIVE_PATH.read_text(encoding="utf-8").strip()
    except Exception:
        return None


EXIT_CHECK = int(os.getenv("EXIT_CHECK_SEC", "60"))     # 손절은 지연이 돈 → 1분 감지
ENTRY_CHECK = int(os.getenv("SIGNAL_ENTRY_CHECK_SEC", "3600"))
RECONCILE = int(os.getenv("RECONCILE_SEC", "300"))
WATCH = int(os.getenv("WATCHDOG_CHECK_SEC", "1800"))    # 상호 감시(hourly 생존) 주기
ROT_CHECK = int(os.getenv("ROTATION_CHECK_SEC", "3600"))   # 로테이션 점검(주1회 자체 게이트, KR 장중 재시도용 1h)


def main() -> None:
    _install_exit_hooks()
    _touch_alive()
    _log(f"start - poll {POLL}s, heartbeat {HEARTBEAT}s, exit {EXIT_CHECK}s, "
         f"entry {ENTRY_CHECK}s, reconcile {RECONCILE}s (pid {os.getpid()})")
    _record("ok", 0, f"worker 시작 (pid {os.getpid()})")
    if os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"):
        import threading

        from src.trading import telegram_cmd

        threading.Thread(target=telegram_cmd.poll_loop, daemon=True).start()
        _log("telegram 명령 폴러 기동 (/잔고 /신호 /킬스위치)")
    last_beat = time.time()
    last_exit = last_entry = last_recon = last_watch = last_rot = last_evt = 0.0
    EVT_CHECK = int(os.getenv("EVENT_ALERT_SEC", "300"))   # 지표·실적 발표 감지 주기
    while True:
        try:
            _touch_alive()                 # 초 단위 생존 흔적 (급사 시각 특정용)
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
                    syms = "·".join(x["symbol"] for x in e.get("entries", []))
                    _record("ok", len(e.get("entries", [])), f"신호진입: {syms} ({e['signal']})")
                    _log(f"신호진입 emit: {syms}")
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
            # 매매 체결 알림 — 새 주문을 전략별로 묶어 텔레그램 (매 폴, 미알림 있을 때만)
            if os.getenv("TELEGRAM_BOT_TOKEN"):
                from src.jobs import trade_alerts

                con_t = db.connect()
                if trade_alerts.notify_new_orders(con_t):
                    _log("매매 알림 발송")
                con_t.close()
            # 주도주 로테이션 — ROTATION_ENABLED=1 일 때만 (모듈이 ISO주당 1회 자체 게이트)
            if os.getenv("ROTATION_ENABLED") == "1" and time.time() - last_rot >= ROT_CHECK:
                from src.trading import leader_rotation

                for mk in ("US", "KR"):
                    res = leader_rotation.evaluate(market=mk)
                    if res and (res.get("enters") or res.get("exits")):
                        _record("ok", len(res["enters"]) + len(res["exits"]),
                                f"로테이션[{mk}] {res['week']}: 진입 {len(res['enters'])} "
                                f"이탈 {len(res['exits'])}")
                        _log(f"로테이션[{mk}] {res['week']}: "
                             f"+{[e['symbol'] for e in res['enters']]} "
                             f"-{[e['symbol'] for e in res['exits']]}")
                        _log(f"로테이션 즉시 처리: {engine.process_once()}")
                last_rot = time.time()
            # 지표·실적 발표 알림 — major 지표 actual 확인 / 감시종목 실적 시간대 (5분 주기)
            if EVT_CHECK > 0 and time.time() - last_evt >= EVT_CHECK and \
                    os.getenv("TELEGRAM_BOT_TOKEN"):
                from src.jobs import event_alerts

                con_e = db.connect()
                sent = event_alerts.check(con_e)
                con_e.close()
                if sent:
                    _log(f"발표 알림 {sent}건 발송")
                last_evt = time.time()
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


def _crash_dump(kind: str, text: str) -> None:
    """최후의 그물 — DB도 _log도 거치지 않고 전용 파일에 직접 쓴다.

    2026-07-28 12시대: `_record` 무예외화 이후에도 워커가 3분마다 재시작했는데,
    루프의 `_log("ERROR"…)`도 atexit의 `stop`도 안 남아 **어디서 끝나는지 알 수 없었다**.
    기존 경로(DB·공용 로그)를 전부 우회하는 독립 기록이 있어야 다음번에 잡는다.
    """
    try:
        with open(db.ROOT / "data" / "engine_crash.log", "a", encoding="utf-8") as f:
            f.write(f"\n=== {datetime.now().isoformat(timespec='seconds')} pid {os.getpid()} "
                    f"[{kind}] ===\n{text}\n")
    except Exception:
        pass


if __name__ == "__main__":
    import sys

    # 스레드에서 터진 예외도(텔레그램 폴러 등) 놓치지 않는다
    sys.excepthook = lambda t, v, tb: _crash_dump(
        "excepthook", "".join(traceback.format_exception(t, v, tb)))
    if hasattr(__import__("threading"), "excepthook"):
        __import__("threading").excepthook = lambda a: _crash_dump(
            "thread", "".join(traceback.format_exception(a.exc_type, a.exc_value, a.exc_traceback)))
    try:
        main()
    except KeyboardInterrupt:
        print("[engine worker] stopped")
        _crash_dump("KeyboardInterrupt", "정상 중단")
    except BaseException:                  # SystemExit·MemoryError 등도 포함
        _crash_dump("main 탈출", traceback.format_exc())
        raise
    else:
        _crash_dump("main 정상 반환", "while 루프가 끝났다 — 있을 수 없는 경로")
