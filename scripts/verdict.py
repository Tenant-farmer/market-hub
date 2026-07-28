"""무인 가동 판정 — 1차(시스템 안정성) / 2차(전략 성과) 자동 채점.

docs/UNATTENDED.md의 합격 기준을 코드로 옮겨, 판정일에 수동 집계 없이 결과를 낸다.
성과 판정은 표본이 모자라면 '판정 보류'로 표기한다(무리한 결론 방지).

실행: python scripts/verdict.py            (1차 + 가능하면 2차)
      python scripts/verdict.py --since 2026-07-23
"""
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")
from src import db  # noqa: E402

START = "2026-07-23"                    # 무인 가동 개시
MIN_EQUITY_DAYS = 20                    # 성과 판정 최소 표본


def _icon(ok):
    return "✅" if ok else "❌"


def system_verdict(con, since: str) -> list[dict]:
    """1차: 시스템 안정성 5+1항목."""
    out = []

    # ① 워치독 경보 — 문서 기준은 "발생 시 **원인 규명·해소하면 그 시점부터 재카운트**"인데
    #    코드는 개시일부터 무조건 세고 있어 문서와 어긋나 있었다(2026-07-28 수정).
    #    해소는 근거를 남겨야 인정한다: status='resolved' 기록에 **원인 문구가 필수**.
    #    남용 방지를 위해 출력에는 전체 건수와 최근 해소 사유를 **항상 같이** 찍는다.
    res = con.execute(
        "SELECT run_at, message FROM collector_runs WHERE collector='watchdog' "
        "AND status='resolved' AND run_at >= ? AND message IS NOT NULL AND message != '' "
        "ORDER BY run_at DESC LIMIT 1", (since,)).fetchone()
    base = max(since, res["run_at"]) if res else since
    n_all = con.execute(
        "SELECT COUNT(*) n FROM collector_runs WHERE collector IN ('watchdog','risk') "
        "AND status='alert' AND run_at >= ?", (since,)).fetchone()["n"]
    n_alert = con.execute(
        "SELECT COUNT(*) n FROM collector_runs WHERE collector IN ('watchdog','risk') "
        "AND status='alert' AND run_at > ?", (base,)).fetchone()["n"]
    detail = f"{n_alert}건"
    if res:
        detail += (f" (전체 {n_all}건 · 최근 해소 {res['run_at'][:16]} — "
                   f"{str(res['message'])[:70]})")
    out.append({"item": "워치독·리스크 경보 0건 (해소 이후)", "ok": n_alert == 0,
                "detail": detail})

    # ② 수집 에러율 — **최근 24시간 기준**.
    #    누적으로 재면 이미 고친 버그가 영원히 카운트돼 판정이 과거에 갇힌다
    #    (실측: 07-27 토큰버그 294건이 수정 후에도 21.8%로 잡힘).
    row = con.execute(
        "SELECT SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) e, COUNT(*) n "
        "FROM collector_runs WHERE run_at >= replace(datetime('now','localtime','-1 day'),' ','T') "
        "AND collector NOT IN ('watchdog','risk')").fetchone()
    rate = (row["e"] or 0) / row["n"] * 100 if row["n"] else 0
    cum = con.execute(
        "SELECT SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) e, COUNT(*) n "
        "FROM collector_runs WHERE run_at >= ? AND collector NOT IN ('watchdog','risk')",
        (since,)).fetchone()
    cum_rate = (cum["e"] or 0) / cum["n"] * 100 if cum["n"] else 0
    out.append({"item": "수집 에러율 < 5% (최근 24h)", "ok": rate < 5,
                "detail": f"24h {rate:.1f}% ({row['e'] or 0}/{row['n']}건) · "
                          f"누적 {cum_rate:.1f}% (수정 전 포함)"})

    # ②-b 전량 실패 수집기 — **집계율만으로는 못 잡는다**.
    #     엔진 하트비트가 분모의 58%(211/361)라, 하루 1회 도는 수집기 7개
    #     (gurus·fed·ecos·earnings·insider·us_stocks·**backup**)가 전부 100% 죽어도
    #     7/361 = 1.9%로 여전히 합격이 나온다(2026-07-28 실측). 백업이 통째로
    #     실패해도 ✅가 뜬다는 뜻 → 수집기별로 성공이 하나라도 있는지 따로 본다.
    #     '24h 안에 돌았는데 성공 0건'만 잡으므로 주기가 다른 수집기에 오탐이 없다.
    dead = con.execute(
        "SELECT collector, COUNT(*) n FROM collector_runs "
        "WHERE run_at >= replace(datetime('now','localtime','-1 day'),' ','T') "
        "AND collector NOT IN ('watchdog','risk') "
        "GROUP BY collector HAVING SUM(CASE WHEN status='ok' THEN 1 ELSE 0 END) = 0"
    ).fetchall()
    out.append({"item": "전량 실패 수집기 0개 (24h)", "ok": not dead,
                "detail": ("없음" if not dead else
                           ", ".join(f"{d['collector']}({d['n']}회 전부 실패)" for d in dead))})

    # ③~④ 스피드테스트는 의도적 반복 매매 → 판정에서 제외
    TEST = "AND (s.source IS NULL OR s.source != 'speed-test')"
    orphan = con.execute(
        "SELECT COUNT(*) n FROM orders o LEFT JOIN signals s ON s.id=o.signal_id "
        "WHERE o.created_at >= ? AND o.signal_id IS NULL "
        "AND o.status NOT IN ('canceled','rejected')", (since,)).fetchone()["n"]
    total_o = con.execute("SELECT COUNT(*) n FROM orders WHERE created_at >= ?",
                          (since,)).fetchone()["n"]
    out.append({"item": "모든 주문이 신호·게이트 경유", "ok": orphan == 0,
                "detail": f"미연결 {orphan}/{total_o}건 (취소·거부 제외)"})

    dup = con.execute(
        "SELECT COUNT(*) n FROM (SELECT o.ticker, o.action, substr(o.created_at,1,10) d, "
        "COUNT(*) c FROM orders o LEFT JOIN signals s ON s.id=o.signal_id "
        f"WHERE o.created_at >= ? AND o.status IN ('filled','submitted') {TEST} "
        "GROUP BY o.ticker, o.action, d HAVING c > 1)", (since,)).fetchone()["n"]
    stale = con.execute(
        "SELECT COUNT(*) n FROM orders WHERE created_at >= ? AND status='stale_replaced'",
        (since,)).fetchone()["n"]
    out.append({"item": "이중주문 0건 (테스트·워치독복구 제외)", "ok": dup == 0,
                "detail": f"중복 의심 {dup}건 · 워치독 재제출 {stale}건"})

    # ⑤ 전략 논거 위반
    try:
        from src.analytics.thesis import check_theses

        th = check_theses(con)
        broken = [t for t in th if t["status"] == "broken"]
        out.append({"item": "전략 논거 위반 0건", "ok": not broken,
                    "detail": (f"{len(th) - len(broken)}/{len(th)} 유효"
                               + (f" — 위반: {', '.join(t['strategy'] for t in broken)}"
                                  if broken else ""))})
    except Exception as e:
        out.append({"item": "전략 논거 점검", "ok": False, "detail": f"실행 실패 {str(e)[:40]}"})

    # ⑥ 엔진 생존 (판정일 기준 오늘 기록)
    last = con.execute("SELECT MAX(run_at) d FROM collector_runs WHERE collector='engine'"
                       ).fetchone()["d"]
    alive = bool(last and last[:10] >= (date.today() - timedelta(days=1)).isoformat())
    out.append({"item": "엔진 워커 생존", "ok": alive,
                "detail": f"마지막 기록 {last[:16] if last else '없음'}"})
    return out


def perf_verdict(con, since: str) -> list[dict]:
    """2차: 전략 성과 — **백테스트 예측 대조**로 재설계(_perf_verdict.py).

    수익률·α의 t값을 20일로 판정하는 건 자기기만이라, 사전 등록한 예측을 실측과
    대조하고 표본이 모자란 항목은 판정하지 않고 참고치로 남긴다.
    """
    from _perf_verdict import perf_verdict as _pv

    return _pv(con, since)


def main():
    since = START
    if "--since" in sys.argv:
        since = sys.argv[sys.argv.index("--since") + 1]
    con = db.connect()
    n_eq = con.execute("SELECT COUNT(DISTINCT date) n FROM portfolio_snapshots").fetchone()["n"]
    print(f"무인 가동 판정 · 개시 {since} · 오늘 {date.today()}")
    print(f"에쿼티 표본 {n_eq}일 (성과 판정 최소 {MIN_EQUITY_DAYS}일)\n")

    print("=" * 68)
    print("1차 — 시스템 안정성 (VPS 이전 판단 기준)")
    print("=" * 68)
    sysv = system_verdict(con, since)
    for r in sysv:
        print(f"  {_icon(r['ok'])} {r['item']:34} {r['detail']}")
    passed = all(r["ok"] for r in sysv)
    print(f"\n  → 1차 판정: {'✅ 합격 — VPS 이전 진행 가능' if passed else '❌ 불합격 — 위 항목 해소 필요'}")

    print("\n" + "=" * 68)
    print("2차 — 전략 성과 (백테스트 예측이 실거동에서 재현되는가)")
    print("=" * 68)
    from _perf_verdict import render

    print(render(perf_verdict(con, since), n_eq))
    con.close()


if __name__ == "__main__":
    main()
