"""아침 상태 리포트 — '내 시스템·계좌'가 어떤 상태인가 (시장 브리핑과 별개).

브리핑(briefing.py)은 **시장**을 보고, 이 리포트는 **우리 시스템**을 본다:
계좌 → 전략별 성과 → 어제 매매 → 전제 점검 → 판정 진행 → 이상 징후.

무인 가동 중 사용자가 폰만 보고 "어제 뭐가 있었고 지금 괜찮은가"를 판단할 수 있게 한다.
발송: hourly 아침 슬롯에서 브리핑 직후 1회 (collector_runs 'status_report'로 멱등).
수동: python -m src.jobs.status_report [--dry]
"""
from datetime import date, datetime, timedelta

VERDICT_DATE = date(2026, 8, 6)          # 1차 판정 (시스템 안정성)
PERF_MIN_DAYS = 20                       # 2차 판정 최소 표본


def _pct(a, b):
    return (a / b - 1) * 100 if b else None


def build_text(con) -> str:
    today = date.today()
    L = [f"<b>📋 상태 리포트</b> · {today.isoformat()} "
         f"({'월화수목금토일'[today.weekday()]})", ""]

    # ---------- 계좌 ----------
    L.append("<b>💼 계좌</b>")
    fx = con.execute("SELECT close FROM prices_daily WHERE symbol='KRW=X' "
                     "ORDER BY date DESC LIMIT 1").fetchone()
    rate = fx["close"] if fx else None
    total_krw = 0.0
    for broker, name, unit in (("kiwoom", "키움", "원"), ("alpaca", "Alpaca", "$")):
        rows = con.execute("SELECT date, equity, pl FROM portfolio_snapshots WHERE broker=? "
                           "ORDER BY date DESC LIMIT 2", (broker,)).fetchall()
        if not rows:
            continue
        cur = rows[0]
        chg = _pct(cur["equity"], rows[1]["equity"]) if len(rows) > 1 else None
        amt = f"{cur['equity']:,.0f}원" if unit == "원" else f"${cur['equity']:,.2f}"
        pl = f"{cur['pl']:+,.0f}원" if unit == "원" else f"${cur['pl']:+,.2f}"
        L.append(f"• {name} {amt}" + (f" ({chg:+.2f}%)" if chg is not None else "")
                 + f" · 미실현 {pl}")
        total_krw += cur["equity"] if unit == "원" else (cur["equity"] * rate if rate else 0)
    if total_krw:
        L.append(f"• 합산 ≈ {total_krw:,.0f}원")
    L.append("")

    # ---------- 전략별 ----------
    L.append("<b>📊 전략</b>")
    try:
        rot = con.execute("SELECT COUNT(*) n FROM rotation_slots").fetchone()["n"]
        rot_kr = con.execute("SELECT COUNT(*) n FROM rotation_slots "
                             "WHERE symbol GLOB '[0-9]*'").fetchone()["n"]
        L.append(f"• 로테이션(실모의): US {rot - rot_kr}슬롯 · KR {rot_kr}슬롯")
    except Exception:
        pass
    for s, lab in (("momentum", "모멘텀"), ("meanrev", "단타")):
        r = con.execute("SELECT equity, n_open FROM daytrade_equity WHERE strategy=? "
                        "ORDER BY date DESC LIMIT 1", (s,)).fetchone()
        if not r:
            continue
        ret = _pct(r["equity"], 100000)
        w = con.execute("SELECT COUNT(*) n, SUM(CASE WHEN pnl_pct>0 THEN 1 ELSE 0 END) w "
                        "FROM daytrade_ledger WHERE strategy=? AND status='closed'",
                        (s,)).fetchone()
        wr = f" · 청산 {w['n']}건 승률 {w['w']/w['n']*100:.0f}%" if w["n"] else ""
        L.append(f"• 가상 {lab}: {ret:+.2f}% · {r['n_open']}종목{wr}")
    L.append("")

    # ---------- 매매 (오늘 있으면 오늘, 없으면 어제) ----------
    # 발송 시각이 16시(KR 마감 후)로 옮겨져 **당일 결산**이 본래 목적이다. 다만 아침에
    # 보충 발송되는 경우엔 오늘 거래가 없으므로 전날을 보여준다 — 라벨로 구분한다.
    def _trades(d):
        return con.execute(
            "SELECT o.ticker, o.action, o.status, o.message, COALESCE(s.source,'') src "
            "FROM orders o LEFT JOIN signals s ON s.id=o.signal_id "
            "WHERE substr(o.created_at,1,10)=? ORDER BY o.id", (d,)).fetchall()

    rows, label = _trades(today.isoformat()), "오늘"
    if not rows:
        rows, label = _trades((today - timedelta(days=1)).isoformat()), "어제"
    if rows:
        L.append(f"<b>🔄 {label} 매매 {len(rows)}건</b>")
        for r in rows[:8]:
            mark = "✅" if r["status"] in ("filled", "submitted", "accepted") else "❌"
            verb = "매수" if r["action"] == "buy" else "매도"
            name = _name(con, r["ticker"])
            L.append(f"{mark} {name} {verb} <i>({r['src'] or '수동'})</i>")
        if len(rows) > 8:
            L.append(f"  … 외 {len(rows) - 8}건")
        L.append("")

    # ---------- 전제 점검 ----------
    try:
        from src.analytics.thesis import check_theses

        th = check_theses(con)
        bad = [t for t in th if t["status"] != "ok"]
        icon = "✅" if not bad else "🔴"
        L.append(f"<b>{icon} 전략 전제 {len(th) - len(bad)}/{len(th)} 유효</b>")
        for t in bad:
            L.append(f"• <b>{t['strategy']}</b>: {t['detail'][:70]}")
        L.append("")
    except Exception:
        pass

    # ---------- 이상 징후 ----------
    warn = []
    try:
        from src.errlog import recent

        sw = recent(con, hours=24)
        if sw:
            warn.append(f"조용한 실패 {len(sw)}건 (최근: {sw[0]['msg'][:50]})")
    except Exception:
        pass
    # 에러는 '마지막 발생 시각'을 함께 — 진행 중인지 이미 해소됐는지 구분 (수정 전 에러가
    # 24h 창에 남아 경보처럼 보이는 문제. 2026-07-27 토큰버그 사례)
    er = con.execute(
        "SELECT COUNT(*) n, MAX(run_at) last FROM collector_runs WHERE status='error' "
        "AND run_at >= replace(datetime('now','localtime','-1 day'),' ','T')").fetchone()
    if er["n"] > 5:
        recent_n = con.execute(
            "SELECT COUNT(*) n FROM collector_runs WHERE status='error' "
            "AND run_at >= replace(datetime('now','localtime','-3 hours'),' ','T')").fetchone()["n"]
        tail = (f"최근 3h {recent_n}건 — 진행 중" if recent_n
                else f"마지막 {er['last'][11:16]} — 이후 없음(해소 추정)")
        warn.append(f"수집 에러 24h {er['n']}건 · {tail}")
    alert = con.execute(
        "SELECT COUNT(*) n FROM collector_runs WHERE collector IN ('watchdog','risk') "
        "AND status='alert' AND run_at >= replace(datetime('now','localtime','-1 day'),' ','T')").fetchone()["n"]
    if alert:
        warn.append(f"워치독·리스크 경보 {alert}건")
    if warn:
        L.append("<b>⚠ 이상 징후</b>")
        L += [f"• {w}" for w in warn]
        L.append("")

    # ---------- 판정 진행 ----------
    n_eq = con.execute("SELECT COUNT(DISTINCT date) n FROM portfolio_snapshots").fetchone()["n"]
    dday = (VERDICT_DATE - today).days
    L.append("<b>🎯 검증 진행</b>")
    L.append(f"• 1차(시스템) {VERDICT_DATE.isoformat()} · <b>D-{dday}</b>"
             if dday > 0 else f"• 1차(시스템) 판정일 도달 — <code>python scripts/verdict.py</code>")
    L.append(f"• 2차(성과) 표본 {n_eq}/{PERF_MIN_DAYS}일"
             + (" — 도달, 판정 가능" if n_eq >= PERF_MIN_DAYS else ""))
    L.append("")
    L.append("<i>/잔고 /신호 /킬스위치 · 대시보드 /positions</i>")
    return "\n".join(L)


def _name(con, code):
    if not str(code).isdigit():
        return code
    for q in ("SELECT name FROM dart_corp WHERE stock_code=?",
              "SELECT name FROM sector_map WHERE stock_code=? LIMIT 1"):
        try:
            r = con.execute(q, (code,)).fetchone()
            if r and r["name"]:
                return r["name"]
        except Exception:
            pass
    return code


def send_report(con) -> int:
    from src import notify

    notify.send(build_text(con))
    return 1


if __name__ == "__main__":
    import sys

    from dotenv import load_dotenv

    load_dotenv()
    sys.path.insert(0, ".")
    from src import db

    c = db.connect()
    if "--dry" in sys.argv:
        import re

        print(re.sub(r"</?[bi]>|<code>|</code>", "", build_text(c)))
    else:
        send_report(c)
        print("발송 완료")
    c.close()
