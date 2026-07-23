"""아침 브리핑 — 대시보드 핵심을 텔레그램 한 통으로.

python -m src.jobs.briefing          # 발송
python -m src.jobs.briefing --dry    # 콘솔 미리보기 (토큰 불필요)
python -m src.jobs.briefing --setup  # 봇에게 말 건 뒤 실행하면 chat_id를 .env에 기록
"""
import argparse
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from src import config, db, notify
from src.dashboard import queries

REGIME = {True: "🟢 200일선 위", False: "🔴 200일선 아래"}


def _fng_label(v: float) -> str:
    if v <= 25:
        return "극단적 공포"
    if v < 45:
        return "공포"
    if v <= 55:
        return "중립"
    if v < 75:
        return "탐욕"
    return "극단적 탐욕"


def _sector_line(rows, names, top=3):
    parts = []
    for r in rows[:top]:
        if r["score"] is None:
            continue
        streak = f" {int(r['streak'])}일" if r.get("streak") else ""
        parts.append(f"{names.get(r['code'], r['code'])}({r['score']:.0f}{streak})")
    return " · ".join(parts)


def _hot_list(rows, names):
    return [names.get(r["code"], r["code"]) for r in rows if r.get("hot")]


def _last2(con, sym, market=None):
    """최근 종가와 전일 대비 % (시세판 라인용)."""
    q = ("SELECT close FROM prices_daily WHERE symbol=? "
         + ("AND market=? " if market else "") + "ORDER BY date DESC LIMIT 2")
    rows = con.execute(q, (sym, market) if market else (sym,)).fetchall()
    if not rows:
        return None, None
    chg = ((rows[0]["close"] / rows[1]["close"] - 1) * 100
           if len(rows) > 1 and rows[1]["close"] else None)
    return rows[0]["close"], chg


def _fmt_px(v, chg, won=False):
    p = f"{v:,.0f}" if (won or v >= 1000) else f"{v:,.2f}"
    return p + (f" {chg:+.2f}%" if chg is not None else "")


def build_text(con) -> str:
    from src.dashboard.fmt import fmt_krw

    today = date.today()
    L = [f"<b>📊 market-hub 시세판</b> · {today.isoformat()} "
         f"({'월화수목금토일'[today.weekday()]})"]
    sig = queries.vix_signal(con)                      # 매수 신호등은 최상단 독립 표기
    if sig:
        L.append(f"🚦 <b>매수 신호등(US): {sig['emoji']} {sig['label']}</b>")
    ksig = queries.kr_signal(con)                      # KR 전용 (VKOSPI≥30 & 낙폭 5%+)
    if ksig:
        L.append(f"🚦 <b>매수 신호등(KR): {ksig['emoji']} {ksig['label']}</b>"
                 f" — VKOSPI {ksig['vkospi']:.0f} · 고점比 {ksig['kospi_dd']:+.1f}%")
    L.append("")
    us_names = config.load()["us"].get("names", {})
    kr_names = queries.kr_index_names(con)
    _, us_rank = queries.ranking(con, "us_sector")
    _, kr_rank = queries.ranking(con, "kr_sector")

    # ---------- 🇰🇷 국장 ----------
    L.append("<b>🇰🇷 국장 (최근 마감)</b>")
    kv, kc = _last2(con, "1001")
    qv, qc = _last2(con, "2001")
    rk = queries.regime(con, "1001")
    parts = []
    if kv:
        parts.append(f"코스피 {_fmt_px(kv, kc, won=True)}"
                     + (" 🟢" if rk and rk["above"] else " 🔴" if rk else ""))
    if qv:
        parts.append(f"코스닥 {_fmt_px(qv, qc)}")
    if parts:
        L.append("• " + " · ".join(parts))
    d0 = con.execute("SELECT MAX(date) d FROM prices_daily WHERE market='KR'").fetchone()["d"]
    d1 = con.execute("SELECT MAX(date) d FROM prices_daily WHERE market='KR' AND date<?",
                     (d0,)).fetchone()["d"] if d0 else None
    if d0 and d1:
        r = con.execute(
            "SELECT SUM(a.close>b.close) up, SUM(a.close=b.close) fl, SUM(a.close<b.close) dn "
            "FROM prices_daily a JOIN prices_daily b ON b.symbol=a.symbol AND b.market='KR' "
            "AND b.date=? WHERE a.market='KR' AND a.date=?", (d1, d0)).fetchone()
        if r and r["up"] is not None:
            L.append(f"• 상승 {r['up']} / 보합 {r['fl']} / 하락 {r['dn']}")
        tops = con.execute(
            "SELECT m.name, p0.close c0, p1.close c1 FROM stock_meta s "
            "JOIN sector_map m ON m.stock_code=s.symbol AND m.market='KR' "
            "JOIN prices_daily p0 ON p0.symbol=s.symbol AND p0.market='KR' AND p0.date=? "
            "JOIN prices_daily p1 ON p1.symbol=s.symbol AND p1.market='KR' AND p1.date=? "
            "WHERE s.mcap IS NOT NULL ORDER BY s.mcap DESC LIMIT 4", (d0, d1)).fetchall()
        if tops:
            L.append("• " + " · ".join(
                f"{t['name']} {t['c0']:,.0f} {(t['c0'] / t['c1'] - 1) * 100:+.1f}%"
                for t in tops if t["c1"]))
    fd = con.execute("SELECT MAX(date) d FROM investor_flows WHERE scope='market'"
                     ).fetchone()["d"]
    if fd:
        fl = {(r["code"], r["investor"]): r["net_value"] for r in con.execute(
            "SELECT code, investor, net_value FROM investor_flows "
            "WHERE scope='market' AND date=?", (fd,))}
        for mkt, ko in (("KOSPI", "코스피"), ("KOSDAQ", "코스닥")):
            trio = [(iko, fl.get((mkt, inv))) for inv, iko in
                    (("individual", "개인"), ("foreign", "외국인"), ("institution", "기관"))]
            if any(v is not None for _, v in trio):
                L.append(f"• 수급({ko}, {fd[5:]}): " + " · ".join(
                    f"{iko} {fmt_krw(v)}" for iko, v in trio if v is not None))
    sf = queries.sector_flows(con, kr_names)
    if sf:
        inflow = " · ".join(f"{s['name']} {s['tot_1w_fmt']}" for s in sf[:2])
        out = sf[-1]
        L.append(f"• 업종 수급(1주): 유입 {inflow} / 유출 {out['name']} {out['tot_1w_fmt']}")
    L.append(f"• 주도 업종: {_sector_line(kr_rank, kr_names)}")
    kr_top = queries.kr_leaders(con, n=5)
    if kr_top:
        L.append("• 주도주: " + " · ".join(r["name"] for r in kr_top))
    L.append("")

    # ---------- 🇺🇸 미국장 ----------
    L.append("<b>🇺🇸 미국장 (최근 마감)</b>")
    sv, sc = _last2(con, "SPY", "US")
    nv, nc = _last2(con, "QQQ", "US")
    rs = queries.regime(con, "SPY")
    parts = []
    if sv:
        parts.append(f"S&P500(SPY) {_fmt_px(sv, sc)}"
                     + (" 🟢" if rs and rs["above"] else " 🔴" if rs else ""))
    if nv:
        parts.append(f"나스닥(QQQ) {_fmt_px(nv, nc)}")
    if parts:
        L.append("• " + " · ".join(parts))
    mega = []
    for sym, nm in (("NVDA", "엔비디아"), ("MSFT", "MS"), ("AAPL", "애플"), ("GOOGL", "알파벳"),
                    ("AMZN", "아마존"), ("TSLA", "테슬라"), ("MU", "마이크론")):
        _, c = _last2(con, sym, "US_STOCK")
        if c is not None:
            mega.append(f"{nm} {c:+.1f}%")
    if mega:
        L.append("• " + " · ".join(mega))
    L.append(f"• 주도 섹터: {_sector_line(us_rank, us_names)}")
    us_top = con.execute(
        """
        SELECT code, MAX(CASE WHEN metric='leader_score' THEN value END) s
        FROM analytics_daily WHERE scope='us_stock'
          AND date=(SELECT MAX(date) FROM analytics_daily WHERE scope='us_stock')
        GROUP BY code ORDER BY s DESC LIMIT 5
        """
    ).fetchall()
    L.append("• 주도주: " + " · ".join(r["code"] for r in us_top))
    hot = _hot_list(us_rank, us_names) + _hot_list(kr_rank, kr_names)
    if hot:
        L.append(f"• ⚠ 과열: {', '.join(hot)}")
    L.append("")

    # ---------- 🌍 컨텍스트 ----------
    L.append("<b>🌍 컨텍스트</b>")
    fx = []
    for sym, nm, dol in (("KRW=X", "원/달러", False), ("DX-Y.NYB", "달러인덱스", False),
                         ("BTC-USD", "BTC", True)):
        v, c = _last2(con, sym, "MACRO")
        if v:
            val = f"${v:,.0f}" if dol else (f"{v:,.2f}" if v < 1000 else f"{v:,.1f}")
            fx.append(f"{nm} {val}" + (f" {c:+.2f}%" if c is not None else ""))
    if fx:
        L.append("• " + " · ".join(fx))
    # VIX·VVIX·VKOSPI는 신호등 줄에, BTC는 환율 줄에 이미 있음 — 매크로 줄에서 중복 제외
    mac = [m for m in (queries.macro_context(con) or [])
           if not any(k in m["label"] for k in ("VIX", "VKOSPI", "BTC"))]
    if mac:
        half = (len(mac) + 1) // 2
        L.append("• " + " · ".join(f"{m['label']} {m['val']}" for m in mac[:half]))
        if mac[half:]:
            L.append("• " + " · ".join(f"{m['label']} {m['val']}" for m in mac[half:]))
    # 한국 거시 (ECOS) — 기준금리·CPI 전년비 (국고금리는 위 매크로 카드에 포함)
    try:
        base_r = con.execute("SELECT close FROM prices_daily WHERE symbol='ECOS:BASE' "
                             "ORDER BY date DESC LIMIT 1").fetchone()
        cpi = con.execute("SELECT date, close FROM prices_daily WHERE symbol='ECOS:CPI' "
                          "ORDER BY date DESC LIMIT 13").fetchall()
        kr_bits = []
        if base_r:
            kr_bits.append(f"한은 기준금리 {base_r['close']:.2f}%")
        if len(cpi) >= 13:
            yoy = (cpi[0]["close"] / cpi[12]["close"] - 1) * 100
            kr_bits.append(f"CPI {yoy:+.1f}% ({int(cpi[0]['date'][5:7])}월, 전년비)")
        if kr_bits:
            L.append("• 🏦 " + " · ".join(kr_bits))
    except Exception:
        pass
    senti = queries.sentiment_latest(con)
    fng = (senti.get("fear_greed") or {}).get("value")
    bits = []
    if sig:
        bits.append(f"VIX {sig['vix']:.1f} · VVIX {sig['vvix']:.0f}")
    if fng is not None:
        bits.append(f"F&G {fng:.0f}({_fng_label(fng)})")
    if bits:
        L.append("• " + " · ".join(bits))
    L.append("")

    # ---------- 📅 일정 (임박) ----------
    earn = [e for e in queries.earnings_upcoming(con, days=2, limit=10) if e["dd"] <= 1]
    seen = set()
    econ = [e for e in queries.econ_upcoming(con, days=1, limit=20)
            if e["major"] and e["dd"] == 0
            and not ((e["event"], e["kst"]) in seen or seen.add((e["event"], e["kst"])))]
    fw = queries.fed_watch(con)
    fomc = fw and fw["next"] and fw["next"]["dday"] <= 1
    if earn or econ or fomc:
        L.append("<b>📅 일정</b>")
        if earn:
            L.append("• 실적: " + " · ".join(
                f"{e['symbol']}({e['dday']} {e['time_ko']})" for e in earn[:6]))
        if econ:
            L.append("• 지표: " + " · ".join(f"{e['event'][:22]}({e['kst']})" for e in econ[:4]))
        if fomc:
            L.append(f"• FOMC {fw['next']['date']} (D-{fw['next']['dday']}) — 결과는 한국시간 새벽")
        L.append("")

    # 뉴스 헤드라인 (최신 4 — 시장·보유종목, news 수집기)
    try:
        import html as _html

        rows = con.execute("SELECT keyword, title, url FROM news WHERE source != 'DART' "
                           "ORDER BY dt DESC LIMIT 4").fetchall()
        if rows:
            L.append("<b>📰 헤드라인</b>")
            for r in rows:
                L.append(f"• [{r['keyword']}] <a href=\"{_html.escape(r['url'])}\">"
                         f"{_html.escape(r['title'][:60])}</a>")
        # 공시 보장 슬롯 — 뉴스에 밀리지 않게 최근 2건 별도 (보유·로테이션 종목)
        drows = con.execute("SELECT keyword, title, url FROM news WHERE source='DART' "
                            "AND dt >= datetime('now','localtime','-2 days') "
                            "ORDER BY dt DESC LIMIT 2").fetchall()
        for r in drows:
            L.append(f"• [{r['keyword']}] <a href=\"{_html.escape(r['url'])}\">"
                     f"{_html.escape(r['title'][:60])}</a>")
    except Exception:
        pass

    # 시스템 상태 한 줄 (무인 가동 관찰용 — 24h 수집·주문·경보·게이트)
    try:
        import os as _os

        c24 = lambda q: con.execute(q).fetchone()["c"]                     # noqa: E731
        ok = c24("SELECT COUNT(*) c FROM collector_runs WHERE status='ok' "
                 "AND run_at >= datetime('now','localtime','-1 day')")
        err = c24("SELECT COUNT(*) c FROM collector_runs WHERE status='error' "
                  "AND run_at >= datetime('now','localtime','-1 day')")
        ords = c24("SELECT COUNT(*) c FROM orders "
                   "WHERE created_at >= datetime('now','localtime','-1 day')")
        wd = c24("SELECT COUNT(*) c FROM collector_runs WHERE collector='watchdog' "
                 "AND run_at >= datetime('now','localtime','-1 day')")
        gates = (f"청산 {'ON' if _os.getenv('EXIT_ENABLED') == '1' else 'off'}"
                 f"·진입 {'ON' if _os.getenv('SIGNAL_ENTRY_ENABLED') == '1' else 'off'}")
        L.append("")
        L.append(f"⚙ 시스템(24h): 수집 ok {ok}{f'/에러 {err}' if err else ''} · 주문 {ords}건"
                 + (f" · 🚨경보 {wd}" if wd else "") + f" · {gates}")
    except Exception:
        pass
    return "\n".join(L)


def send_briefing(con) -> int:
    notify.send(build_text(con))
    return 1


def _setup_chat_id() -> None:
    cid = notify.discover_chat_id()
    if not cid:
        print("chat_id를 못 찾음 — 텔레그램에서 봇에게 아무 메시지나 먼저 보내세요.")
        return
    env_path = Path(__file__).resolve().parents[2] / ".env"
    lines = env_path.read_text(encoding="utf-8").splitlines()
    lines = [l for l in lines if not l.startswith("TELEGRAM_CHAT_ID=")]
    lines.append(f"TELEGRAM_CHAT_ID={cid}")
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"TELEGRAM_CHAT_ID={cid} 를 .env에 기록했습니다.")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry", action="store_true", help="발송 없이 콘솔 미리보기")
    p.add_argument("--setup", action="store_true", help="chat_id 자동 탐지 후 .env 기록")
    args = p.parse_args()
    if args.setup:
        _setup_chat_id()
    else:
        con = db.connect()
        text = build_text(con)
        con.close()
        if args.dry:
            print(text)
        else:
            notify.send(text)
            print("발송 완료")
