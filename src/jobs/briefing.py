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


def build_text(con) -> str:
    today = date.today().isoformat()
    L = [f"<b>📊 market-hub 브리핑</b> · {today}", ""]

    # 시장 온도
    spy, kospi = queries.bench_snapshot(con, "SPY"), queries.bench_snapshot(con, "1001")
    rs, rk = queries.regime(con, "SPY"), queries.regime(con, "1001")
    senti = queries.sentiment_latest(con)
    if spy:
        L.append(f"S&P500 {spy['close']:,.0f} ({spy['ret21']:+.1f}% 1M) {REGIME[rs['above']] if rs else ''}")
    if kospi:
        L.append(f"코스피 {kospi['close']:,.0f} ({kospi['ret21']:+.1f}% 1M) {REGIME[rk['above']] if rk else ''}")
    fng = (senti.get("fear_greed") or {}).get("value")
    vix = (senti.get("vix") or {}).get("value")
    if fng or vix:
        L.append((f"F&G {fng:.0f} ({_fng_label(fng)})" if fng else "")
                 + (" · " if fng and vix else "")
                 + (f"VIX {vix:.1f}" if vix else ""))
    sig = queries.vix_signal(con)
    if sig:
        L.append(f"매수 신호등: {sig['emoji']} {sig['label']} (VVIX {sig['vvix']:.0f})")
    L.append("")

    # 주도 섹터
    us_names = config.load()["us"].get("names", {})
    kr_names = queries.kr_index_names(con)
    _, us_rank = queries.ranking(con, "us_sector")
    _, kr_rank = queries.ranking(con, "kr_sector")
    L.append(f"<b>US 주도</b>: {_sector_line(us_rank, us_names)}")
    L.append(f"<b>KR 주도</b>: {_sector_line(kr_rank, kr_names)}")
    hot = _hot_list(us_rank, us_names) + _hot_list(kr_rank, kr_names)
    if hot:
        L.append(f"⚠ 과열: {', '.join(hot)}")
    L.append("")

    # KR 수급 (1주)
    mf = {(m["mkt"], m["inv_ko"]): m for m in queries.market_flows(con)}
    kf, ki = mf.get(("KOSPI", "외국인")), mf.get(("KOSPI", "기관"))
    if kf and ki:
        L.append(f"<b>KOSPI 수급(5일)</b>: 외인 {kf['d5_fmt']} · 기관 {ki['d5_fmt']}")
    sf = queries.sector_flows(con, kr_names)
    if sf:
        inflow = " · ".join(f"{s['name']} {s['tot_1w_fmt']}" for s in sf[:2])
        out = sf[-1]
        L.append(f"업종 유입: {inflow} / 유출: {out['name']} {out['tot_1w_fmt']}")
    L.append("")

    # 실적·지표 일정 (임박 2일)
    earn = [e for e in queries.earnings_upcoming(con, days=2, limit=10) if e["dd"] <= 1]
    if earn:
        L.append("📅 실적: " + " · ".join(f"{e['symbol']}({e['dday']} {e['time_ko']})" for e in earn[:6]))
    econ = [e for e in queries.econ_upcoming(con, days=1, limit=20) if e["major"] and e["dd"] == 0]
    if econ:
        L.append("📈 지표: " + " · ".join(f"{e['event'][:22]}({e['kst']})" for e in econ[:4]))
    if earn or econ:
        L.append("")

    # 주도주 TOP5
    us_top = con.execute(
        """
        SELECT code, MAX(CASE WHEN metric='leader_score' THEN value END) s
        FROM analytics_daily WHERE scope='us_stock'
          AND date=(SELECT MAX(date) FROM analytics_daily WHERE scope='us_stock')
        GROUP BY code ORDER BY s DESC LIMIT 5
        """
    ).fetchall()
    L.append("<b>US 주도주</b>: " + " · ".join(r["code"] for r in us_top))
    kr_top = queries.kr_leaders(con, n=5)
    if kr_top:
        L.append("<b>KR 주도주</b>: " + " · ".join(r["name"] for r in kr_top))
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
