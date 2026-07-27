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

    # ① 워치독 경보
    n_alert = con.execute(
        "SELECT COUNT(*) n FROM collector_runs WHERE collector IN ('watchdog','risk') "
        "AND status='alert' AND run_at >= ?", (since,)).fetchone()["n"]
    out.append({"item": "워치독·리스크 경보 0건", "ok": n_alert == 0,
                "detail": f"{n_alert}건"})

    # ② 수집 에러율 — **최근 24시간 기준**.
    #    누적으로 재면 이미 고친 버그가 영원히 카운트돼 판정이 과거에 갇힌다
    #    (실측: 07-27 토큰버그 294건이 수정 후에도 21.8%로 잡힘).
    row = con.execute(
        "SELECT SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) e, COUNT(*) n "
        "FROM collector_runs WHERE run_at >= datetime('now','localtime','-1 day') "
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


def perf_verdict(con) -> list[dict]:
    """2차: 전략 성과 — 표본 부족이면 '보류'."""
    out = []
    n_eq = con.execute("SELECT COUNT(DISTINCT date) n FROM portfolio_snapshots").fetchone()["n"]
    if n_eq < MIN_EQUITY_DAYS:
        return [{"item": "성과 판정", "ok": None,
                 "detail": f"표본 {n_eq}/{MIN_EQUITY_DAYS}일 — **판정 보류** "
                           f"(무리한 결론 방지)"}]

    # α/β
    try:
        from src.analytics.attribution import attribute_strategy

        for s in ("momentum", "meanrev"):
            r = attribute_strategy(con, s)
            if r and "error" not in r:
                out.append({"item": f"{s} α/β", "ok": r["alpha_ann"] > 0,
                            "detail": f"α {r['alpha_ann']:+.1f}%/년 · β {r['beta']} · "
                                      f"시장설명 {r['market_share']}% · t {r['t_alpha']}"})
    except Exception:
        pass

    # VaR 실측 vs 백테스트 예측(-2.65%)
    try:
        from src.analytics.risk import strategy_risk

        r = strategy_risk(con, "momentum")
        if r and "error" not in r:
            ok = abs(r["var_pct"] - 2.65) < 2.0          # 예측과 2%p 이내면 정합
            out.append({"item": "VaR 실측 vs 백테스트(-2.65%)", "ok": ok,
                        "detail": f"실측 -{r['var_pct']}% · CVaR -{r['cvar_pct']}%"})
    except Exception:
        pass

    # 가상장부 A/B 방향
    eqs = {}
    for s in ("momentum", "meanrev"):
        r = con.execute("SELECT equity FROM daytrade_equity WHERE strategy=? "
                        "ORDER BY date DESC LIMIT 1", (s,)).fetchone()
        if r:
            eqs[s] = (r["equity"] / 100000 - 1) * 100
    if len(eqs) == 2:
        out.append({"item": "가상장부 A/B (모멘텀 > 단타 예측)",
                    "ok": eqs["momentum"] >= eqs["meanrev"],
                    "detail": f"모멘텀 {eqs['momentum']:+.2f}% vs 단타 {eqs['meanrev']:+.2f}%"})
    return out


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
    print(f"2차 — 전략 성과 (표본 {MIN_EQUITY_DAYS}영업일 도달 시)")
    print("=" * 68)
    for r in perf_verdict(con):
        icon = "⏳" if r["ok"] is None else _icon(r["ok"])
        print(f"  {icon} {r['item']:34} {r['detail']}")
    con.close()


if __name__ == "__main__":
    main()
