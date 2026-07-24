"""일일 매매일지 자동 생성 — docs/journal/YYYY-MM-DD.md.

hourly가 매 실행마다 당일 파일을 덮어써 항상 최신 상태 유지, 자정이 지나면 확정.
DB만 읽음(브로커 API 추가 호출 없음 — 계좌는 portfolio_snapshots 재사용).
2주 무인 가동(2026-07-23~08-06)의 일별 판정 근거자료. 수동 실행: python -m src.jobs.journal
"""
from datetime import date, datetime, timedelta
from pathlib import Path

DIR = Path(__file__).resolve().parents[2] / "docs" / "journal"
WD = "월화수목금토일"


def _last2(con, sym):
    rows = con.execute("SELECT date, close FROM prices_daily WHERE symbol=? "
                       "ORDER BY date DESC LIMIT 2", (sym,)).fetchall()
    if not rows:
        return None, None
    chg = ((rows[0]["close"] / rows[1]["close"] - 1) * 100) if len(rows) > 1 else None
    return rows[0]["close"], chg


def write_today(con) -> str:
    from src.dashboard.queries_macro import kr_signal, vix_signal

    today = date.today()
    t = today.isoformat()
    L = [f"# 매매일지 {t} ({WD[today.weekday()]})", ""]

    # ---- 신호등 ----
    L.append("## 신호등")
    us = vix_signal(con)
    if us:
        L.append(f"- US: {us['emoji']} {us['label']} (VIX {us['vix']:.1f} · VVIX {us['vvix']:.0f})")
    kr = kr_signal(con)
    if kr:
        L.append(f"- KR: {kr['emoji']} {kr['label']} "
                 f"(VKOSPI {kr['vkospi']:.1f} · 고점比 {kr['kospi_dd']:+.1f}%)")
    L.append("")

    # ---- 계좌 (스냅샷 + 전일比) ----
    L.append("## 계좌")
    L.append("| 계좌 | 총자산 | 전일比 | 미실현손익 |")
    L.append("|---|---|---|---|")
    for broker, name, fmt in (("kiwoom", "키움 모의", "{:,.0f}원"),
                              ("alpaca", "Alpaca", "${:,.2f}")):
        cur = con.execute("SELECT equity, pl FROM portfolio_snapshots WHERE broker=? AND date=?",
                          (broker, t)).fetchone()
        prev = con.execute("SELECT equity FROM portfolio_snapshots WHERE broker=? AND date<? "
                           "ORDER BY date DESC LIMIT 1", (broker, t)).fetchone()
        if not cur:
            L.append(f"| {name} | – | – | – |")
            continue
        chg = (f"{(cur['equity'] / prev['equity'] - 1) * 100:+.2f}%"
               if prev and prev["equity"] else "–")
        L.append(f"| {name} | {fmt.format(cur['equity'])} | {chg} | {fmt.format(cur['pl'])} |")
    L.append("")

    # ---- 오늘의 주문 ----
    orders = con.execute(
        "SELECT created_at, broker, ticker, action, qty, price, status FROM orders "
        "WHERE substr(created_at,1,10)=? ORDER BY id", (t,)).fetchall()
    L.append(f"## 주문 ({len(orders)}건)")
    if orders:
        L.append("| 시각 | 브로커 | 종목 | 구분 | 수량 | 가격 | 상태 |")
        L.append("|---|---|---|---|---|---|---|")
        for o in orders:
            px = f"{o['price']:,.2f}" if o["price"] else "–"
            L.append(f"| {o['created_at'][11:16]} | {o['broker']} | {o['ticker']} "
                     f"| {o['action']} | {o['qty'] or '–'} | {px} | {o['status']} |")
    else:
        L.append("- 주문 없음")
    L.append("")

    # ---- 신호 수신 ----
    sigs = con.execute(
        "SELECT received_at, source, ticker, action, qty, strategy, status FROM signals "
        "WHERE substr(received_at,1,10)=? ORDER BY id", (t,)).fetchall()
    L.append(f"## 신호 수신 ({len(sigs)}건)")
    for s in sigs:
        L.append(f"- {s['received_at'][11:16]} [{s['source'] or '-'}] {s['ticker']} "
                 f"{s['action']} x{s['qty'] or '-'} → {s['status']}"
                 + (f" ({s['strategy']})" if s["strategy"] else ""))
    if not sigs:
        L.append("- 신호 없음")
    L.append("")

    # ---- 매매 근거 (신호 소스별 — 왜 샀/팔았나) ----
    def _kname(code):
        try:
            r = con.execute("SELECT name FROM dart_corp WHERE stock_code=?", (code,)).fetchone()
            return r["name"] if r else code
        except Exception:
            return code

    L.append("## 매매 근거")
    if not sigs:
        L.append("- 매매 없음")
    by_src = {}
    for s in sigs:
        by_src.setdefault(s["source"] or "-", []).append(s)
    for src, items in by_src.items():
        if src == "rotation":
            for tag, grp in (("US", [s for s in items if not str(s["ticker"]).isdigit()]),
                             ("KR", [s for s in items if str(s["ticker"]).isdigit()])):
                if not grp:
                    continue
                nb = sum(1 for s in grp if s["action"] == "buy")
                ns = len(grp) - nb
                note = ("백테스트 2010~ 생존 전략 (+6,042%, 서바이버십 유의)" if tag == "US"
                        else "KR은 백테스트 실패(-61~-74%) 전략 — 미국과의 A/B 검증용 모의")
                L.append(f"- **주도주 로테이션 {tag}** ({nb}매수·{ns}매도): 126일 수익률 상위"
                         f" 10 편입 / top30 이탈 시 교체, 주 1회 평가 — {note}")
                for s in grp:
                    nm = _kname(s["ticker"]) if tag == "KR" else s["ticker"]
                    L.append(f"  - {nm} {s['action']}: {s['strategy'] or ''}")
        elif src == "signal-entry":
            for s in items:
                label = (s["strategy"] or "").split(":", 1)[-1]
                if str(s["ticker"]).isdigit():
                    ctx = (f"VKOSPI {kr['vkospi']:.1f}(≥30) & 고점比 {kr['kospi_dd']:+.1f}%(≤-5%)"
                           if kr else "VKOSPI 조건 충족")
                    base = "2010~24 백테스트: +63일 승률 75% · 중앙 +5.3% · 저점지연 22일"
                else:
                    ctx = (f"VIX {us['vix']:.1f} · VVIX {us['vvix']:.0f}" if us else "글로벌 공포")
                    base = "2007~ 백테스트: 즉시 진입 승률 78%"
                L.append(f"- **신호진입** {_kname(s['ticker'])} {s['action']}: {label} — {ctx}. "
                         f"{base}. green 지속 시 매일 1주 분할 진입")
        elif src == "exit":
            for s in items:
                L.append(f"- **청산** {_kname(s['ticker'])} {s['action']}: {s['strategy'] or ''}"
                         " — 원금 방어 규칙 (손절 -8% / 주도이탈 rs<0)")
        elif src == "speed-test":
            L.append(f"- **스피드테스트** ({len(items)}건): 주문 경로 왕복속도 측정용 — 전략 아님")
        else:
            for s in items:
                L.append(f"- **{src}** {s['ticker']} {s['action']}: {s['strategy'] or '(수동/외부 신호)'}")
    L.append("")

    # ---- 경보 / 특이사항 ----
    alerts = con.execute(
        "SELECT run_at, collector, message FROM collector_runs "
        "WHERE collector IN ('watchdog','risk') AND substr(run_at,1,10)=? ORDER BY run_at",
        (t,)).fetchall()
    stale = con.execute(
        "SELECT ticker FROM orders WHERE status='stale_replaced' AND substr(created_at,1,10)=?",
        (t,)).fetchall()
    L.append("## 경보 · 특이사항")
    for a in alerts:
        L.append(f"- {a['run_at'][11:16]} [{a['collector']}] {a['message']}")
    for s in stale:
        L.append(f"- 매도 워치독 재제출: {s['ticker']}")
    if not alerts and not stale:
        L.append("- 없음")
    L.append("")

    # ---- 가상장부 A/B (모멘텀·단타) ----
    try:
        vrows = con.execute("SELECT DISTINCT strategy FROM daytrade_ledger").fetchall()
    except Exception:
        vrows = []
    if vrows:
        L.append("## 가상장부 A/B (실주문 없음, 시드 $100k)")
        LABV = {"momentum": "모멘텀(공격)", "meanrev": "단타(급락반등)"}
        for r in vrows:
            s = r["strategy"]
            eq = con.execute("SELECT equity, n_open FROM daytrade_equity WHERE strategy=? "
                             "ORDER BY date DESC LIMIT 1", (s,)).fetchone()
            if not eq:
                continue
            tc = con.execute("SELECT symbol, exit_reason, pnl_pct FROM daytrade_ledger "
                             "WHERE strategy=? AND status='closed' AND exit_date=?",
                             (s, t)).fetchall()
            to = con.execute("SELECT symbol FROM daytrade_ledger WHERE strategy=? "
                             "AND status='open' AND entry_date=?", (s, t)).fetchall()
            ret = (eq["equity"] / 100000 - 1) * 100
            L.append(f"- **{LABV.get(s, s)}**: ${eq['equity']:,.0f} ({ret:+.2f}%) · 보유 {eq['n_open']}종목")
            if to:
                L.append(f"  - 진입: {', '.join(x['symbol'] for x in to)}")
            for c in tc:
                L.append(f"  - 청산: {c['symbol']} {c['pnl_pct']:+.1f}% ({c['exit_reason']})")
        L.append("")

    # ---- 시장 한 줄 ----
    bits = []
    for sym, nm, f in (("1001", "KOSPI", "{:,.0f}"), ("2001", "KOSDAQ", "{:,.0f}"),
                       ("SPY", "SPY", "${:,.2f}"), ("VKOSPI", "VKOSPI", "{:.1f}"),
                       ("^VIX", "VIX", "{:.1f}")):
        v, c = _last2(con, sym)
        if v is not None:
            bits.append(f"{nm} {f.format(v)}" + (f" ({c:+.2f}%)" if c is not None else ""))
    L.append("## 시장")
    L.append("- " + " · ".join(bits))
    L.append("")
    L.append(f"*갱신 {datetime.now().strftime('%H:%M')} — 매시 자동, 자정 후 확정*")

    DIR.mkdir(parents=True, exist_ok=True)
    path = DIR / f"{t}.md"
    path.write_text("\n".join(L), encoding="utf-8")
    return str(path)


if __name__ == "__main__":
    import sys

    from dotenv import load_dotenv

    load_dotenv()
    sys.path.insert(0, ".")
    from src import db

    c = db.connect()
    print("생성:", write_today(c))
    c.close()
