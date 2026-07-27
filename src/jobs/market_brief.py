"""시황 브리핑 — 사용자 요청 포맷 (3대지수·크립토·환율·금리·금·유가 + 주요 이슈).

기존 브리핑(briefing.py)이 '우리 관점의 시세판'이라면, 이건 **표준 시황 리포트 포맷**이다.
전일 종가 대비 변동을 🔺/🔽와 함께 절대값·퍼센트로 표기한다.

헤드라인: 자동 생성 — 당일 최대 변동 자산 + 관련 뉴스 제목을 결합(과장 없이 사실만).
주요 이슈: ①임박 주요 경제지표(FOMC 등) ②당일·익일 대형 실적 ③최신 헤드라인 순.

데이터: prices_daily (macro 수집기가 채움). 발송: hourly 아침 슬롯.
수동: python -m src.jobs.market_brief [--dry]
"""
from datetime import date, datetime, timedelta

# (심볼, 표시명, 소수자리, 그룹) — 그룹이 바뀌면 빈 줄
ROWS = [
    ("^DJI", "DOW", 2, "idx"),
    ("^IXIC", "NASDAQ", 2, "idx"),
    ("^GSPC", "S&P 500", 2, "idx"),
    ("BTC-USD", "BTC", 2, "crypto"),
    ("ETH-USD", "ETH", 2, "crypto"),
    ("DX-Y.NYB", "달러 인덱스", 3, "fx"),
    ("KRW=X", "달러/원 환율", 2, "fx"),
    ("2YY=F", "2년물 금리", 3, "bond"),
    ("^TNX", "10년물 금리", 3, "bond"),
    ("GC=F", "금", 3, "gold"),
    ("CL=F", "WTI", 2, "oil"),
    ("BZ=F", "브렌트", 2, "oil"),
]
GROUP_TITLE = {"bond": "<b>미 국채</b>", "oil": "<b>유가</b>"}


def _quote(con, sym):
    """최근 2거래일 종가 → (현재, 변화, 변화율%). 없으면 None."""
    rows = con.execute(
        "SELECT date, close FROM prices_daily WHERE symbol=? AND close IS NOT NULL "
        "ORDER BY date DESC LIMIT 2", (sym,)).fetchall()
    if len(rows) < 2:
        return None
    cur, prev = rows[0]["close"], rows[1]["close"]
    return cur, cur - prev, (cur / prev - 1) * 100 if prev else 0.0


def _line(name, q, dp):
    cur, chg, pct = q
    arrow = "🔺" if chg >= 0 else "🔽"
    return (f"{name} : {arrow}{cur:,.{dp}f} "
            f"({chg:+,.{dp}f}, {pct:+.2f}%)")


def _headline(con, quotes: dict) -> str:
    """당일 최대 변동 자산 + 관련 뉴스로 헤드라인 구성 (사실 기반, 추측 금지)."""
    movers = [(abs(q[2]), name, q) for name, q in quotes.items() if q]
    if not movers:
        return ""
    movers.sort(reverse=True)
    _, name, q = movers[0]
    direction = "급등" if q[2] >= 3 else "상승" if q[2] > 0 else "급락" if q[2] <= -3 else "하락"
    # 관련 뉴스 (있으면 제목 앞부분만 인용)
    kw = {"WTI": "유가", "브렌트": "유가", "금": "금", "BTC": "비트코인",
          "달러/원 환율": "환율", "NASDAQ": "나스닥", "DOW": "다우", "S&P 500": "증시"}.get(name)
    news = ""
    if kw:
        r = con.execute(
            "SELECT title FROM news WHERE title LIKE ? AND source != 'DART' "
            "AND dt >= datetime('now','localtime','-2 days') ORDER BY dt DESC LIMIT 1",
            (f"%{kw}%",)).fetchone()
        if r:
            news = _cut(r["title"], 64)
    head = f"{name} {q[2]:+.2f}% {direction}"
    return f"1) {news + ' — ' if news else ''}{head}"


def _cut(s: str, n: int) -> str:
    """단어 중간에서 자르지 않기 — 구두점·공백 경계 우선.

    뉴스 제공처(네이버 등)가 이미 '...'로 자른 제목은 말줄임을 하나로 정돈한다.
    """
    s = s.strip()
    trimmed = False
    while s.endswith((".", "…", " ")):          # 원본 말줄임 제거 (뒤에서 하나로 재부착)
        s, trimmed = s[:-1].rstrip(), True
    if len(s) <= n:
        return s + ("…" if trimmed else "")
    cut = s[:n]
    for sep in ("…", ". ", ", ", " "):
        i = cut.rfind(sep)
        if i > n * 0.6:
            return cut[:i].rstrip(" ,.") + "…"
    return cut.rstrip() + "…"


def _issues(con) -> list[str]:
    """주요 이슈 — FOMC급(7일) → 일반 major(2일) → 대형 실적 → 최신 헤드라인.

    FOMC·금리결정은 시장 최대 변수라 2일 창을 벗어나도 잡아야 한다(실측: 7/30 FOMC가
    2일 창에서 누락됨). 같은 이벤트가 여러 날 반복되는 건(GDPNow 등) 최초 1건만.
    """
    out, seen = [], set()
    today = date.today().isoformat()
    ahead = (date.today() + timedelta(days=2)).isoformat()
    week = (date.today() + timedelta(days=7)).isoformat()

    def _add_event(r, icon="🇺🇸"):
        name = r["event"]
        if name in seen:
            return
        seen.add(name)
        flag = "🇺🇸" if r["country"] == "US" else "🇰🇷"
        d = "오늘" if r["date"] == today else r["date"][5:].replace("-", "/")
        out.append(f"{icon if icon != '🇺🇸' else flag} {d} {name}")

    try:                                        # ① FOMC·금리결정 (7일 창, 최우선)
        for r in con.execute(
                "SELECT date, event, country FROM econ_calendar WHERE date BETWEEN ? AND ? "
                "AND (event LIKE '%FOMC%' OR event LIKE '%Interest Rate%') "
                "ORDER BY date LIMIT 2", (today, week)):
            _add_event(r, "🏛")
    except Exception:
        pass
    try:                                        # ② 그 외 major 지표 (2일 창)
        for r in con.execute(
                "SELECT date, event, country FROM econ_calendar WHERE major=1 "
                "AND date BETWEEN ? AND ? ORDER BY date LIMIT 6", (today, ahead)):
            _add_event(r)
            if len(out) >= 3:
                break
    except Exception:
        pass
    try:                                        # ② 대형 실적 (감시 종목)
        rows = con.execute(
            "SELECT date, symbol, eps_forecast FROM earnings_calendar "
            "WHERE date BETWEEN ? AND ? ORDER BY date LIMIT 4", (today, ahead)).fetchall()
        syms = [r["symbol"] for r in rows]
        if syms:
            d = rows[0]["date"][5:].replace("-", "/")
            out.append(f"📈 {d} 실적: {', '.join(syms)}")
    except Exception:
        pass
    try:                                        # ③ 최신 헤드라인
        for r in con.execute(
                "SELECT title FROM news WHERE source != 'DART' "
                "AND dt >= datetime('now','localtime','-1 day') ORDER BY dt DESC LIMIT 2"):
            out.append(f"📰 {_cut(r['title'], 62)}")
    except Exception:
        pass
    return out[:5]


def build_text(con) -> str:
    d = date.today()
    L = [f"<b>📈 {d.month}/{d.day} 시황 브리핑</b>", ""]
    quotes, lines, last_group = {}, [], None
    for sym, name, dp, grp in ROWS:
        q = _quote(con, sym)
        quotes[name] = q
        if not q:
            continue
        if last_group and grp != last_group:
            lines.append("")
        if grp in GROUP_TITLE and grp != last_group:
            lines.append(GROUP_TITLE[grp])
        lines.append(_line(name, q, dp))
        last_group = grp
    head = _headline(con, quotes)
    if head:
        L += [head, ""]
    L += lines
    issues = _issues(con)
    if issues:
        L += ["", "<b>주요 이슈</b>"]
        L += [f"{i + 1}. {x}" for i, x in enumerate(issues)]
    missing = [n for n, q in quotes.items() if not q]
    if missing:
        L += ["", f"<i>미수집: {', '.join(missing)}</i>"]
    return "\n".join(L)


def send_brief(con) -> int:
    from src import notify

    notify.send(build_text(con))
    return 1


if __name__ == "__main__":
    import re
    import sys

    from dotenv import load_dotenv

    load_dotenv()
    sys.path.insert(0, ".")
    from src import db

    c = db.connect()
    if "--dry" in sys.argv:
        print(re.sub(r"</?[bi]>", "", build_text(c)))
    else:
        send_brief(c)
        print("발송 완료")
    c.close()
