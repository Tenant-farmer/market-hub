"""hourly(:05) 수집 구간이 엔진에 미친 영향 점검 — 재시작·락·크래시를 한 번에 본다.

왜 필요한가 (2026-07-28): DB 락으로 워커가 죽던 문제를 고친 뒤 **정말 안 죽는지**는
다음 :05 구간을 지나봐야 안다. 매번 손으로 쿼리 4개를 치는 대신 한 줄로 확인한다.

사용: python scripts/check_hourly_impact.py [HH]     (기본: 직전 시각)
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")
from src import db  # noqa: E402


def main() -> int:
    now = datetime.now()
    hh = int(sys.argv[1]) if len(sys.argv) > 1 else now.hour
    day = now.date().isoformat()
    lo, hi = f"{day}T{hh:02d}:00", f"{day}T{hh:02d}:59"

    con = db.connect()
    starts = con.execute(
        "SELECT run_at, message FROM collector_runs WHERE collector='engine' "
        "AND message LIKE 'worker 시작%' AND run_at BETWEEN ? AND ? ORDER BY run_at",
        (lo, hi)).fetchall()
    # 런처와 실제 인터프리터가 각각 기록해 **1회 기동에 2건**이 남는다(Store-Python venv)
    cycles = sorted({r["run_at"][:16] for r in starts})
    errs = con.execute(
        "SELECT COUNT(*) c FROM collector_runs WHERE collector='engine' AND status='error' "
        "AND run_at BETWEEN ? AND ?", (lo, hi)).fetchone()["c"]
    alerts = con.execute(
        "SELECT COUNT(*) c FROM collector_runs WHERE collector IN ('watchdog','risk') "
        "AND status='alert' AND run_at BETWEEN ? AND ?", (lo, hi)).fetchone()["c"]
    coll = con.execute(
        "SELECT COUNT(*) n, SUM(CASE WHEN status='ok' THEN 1 ELSE 0 END) ok "
        "FROM collector_runs WHERE collector NOT IN ('engine','watchdog','risk') "
        "AND run_at BETWEEN ? AND ?", (lo, hi)).fetchone()
    con.close()

    alive = ROOT / "data" / "engine_alive.txt"
    age = None
    if alive.exists():
        age = (datetime.now() - datetime.fromisoformat(
            alive.read_text(encoding="utf-8").strip())).total_seconds()

    ok = not cycles and errs == 0 and alerts == 0 and (age is not None and age < 90)
    print(f"[{hh:02d}:05 점검] 워커 재시작 {len(cycles)}회 · "
          f"락에러 {errs}건 · 워치독 {alerts}건 · "
          f"수집 {coll['ok'] or 0}/{coll['n']}건 · "
          f"엔진 폴 {f'{age:.0f}초 전' if age is not None else '기록없음'}", flush=True)
    for c in cycles:
        print(f"  재시작: {c}", flush=True)

    crash = ROOT / "data" / "engine_crash.log"
    if crash.exists():
        recent = [b for b in crash.read_text(encoding="utf-8", errors="replace").split("===")
                  if f"{day}T{hh:02d}:" in b]
        if recent:
            print(f"  ⚠ 크래시 덤프 {len(recent)}건 — 종료 지점 확보:", flush=True)
            for line in "===".join(recent[-1:]).strip().splitlines()[:14]:
                print(f"    {line}", flush=True)
        else:
            print("  크래시 덤프: 이 시간대 없음", flush=True)
    else:
        print("  크래시 덤프: 파일 없음 (한 번도 안 죽음)", flush=True)

    print(f"  => {'✅ 무사 통과' if ok else '❌ 문제 있음 — 위 내역 확인'}", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
