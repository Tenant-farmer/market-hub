"""일정 조회 — Fed Watch, 경제지표, 실적 캘린더.

queries.py에서 도메인 분리 (함수 이동, 로직 불변). queries가 re-export한다.
"""
from src import config
from src.dashboard.fmt import fmt_usd

EARN_TIME_KO = {
    "time-pre-market": "장전",
    "time-after-hours": "장마감 후",
    "time-not-supplied": "미정",
}


def fed_watch(con):
    """Fed Watch — 현재 목표금리·다음 FOMC·추이·2026 일정·변동 이력."""
    from datetime import date

    rows = con.execute(
        "SELECT date, close FROM prices_daily WHERE symbol='DFEDTARU' ORDER BY date"
    ).fetchall()
    if len(rows) < 30:
        return None
    series = [{"time": r["date"], "value": r["close"]} for r in rows]
    by_date = {r["date"]: r["close"] for r in rows}
    dates = [r["date"] for r in rows]
    cur = rows[-1]["close"]

    today = date.today()
    meetings = []
    next_meeting = None
    for m in config.load()["fed"]["meetings"]:
        md = date.fromisoformat(m)
        past = md < today
        after = next((by_date[d] for d in dates if d > m), None)
        before = next((by_date[d] for d in reversed(dates) if d <= m), None)
        chg = None
        if past and after is not None and before is not None:
            bp = round((after - before) * 100)
            chg = "동결" if bp == 0 else (f"{abs(bp)}bp 인하" if bp < 0 else f"{bp}bp 인상")
        status = "완료" if past else ("다음" if next_meeting is None else "예정")
        if status == "다음":
            next_meeting = {"date": m, "dday": (md - today).days}
        meetings.append({
            "date": m, "status": status,
            "rate": f"{after:.2f}%" if past and after is not None else "–",
            "chg": chg or "–",
        })

    changes = []
    prev = None
    for r in rows:
        if prev is not None and r["close"] != prev:
            changes.append({"date": r["date"][:7], "rate": f"{r['close']:.2f}%"})
        prev = r["close"]
    return {
        "cur": cur, "series": series, "meetings": meetings,
        "next": next_meeting, "changes": changes[-8:][::-1],
    }


def econ_upcoming(con, days: int = 7, limit: int = 12) -> list[dict]:
    """향후 경제지표 (주요 지표 우선, KST 시각)."""
    from datetime import date, datetime, timedelta

    today = date.today()
    try:
        rows = con.execute(
            """
            SELECT date, gmt, country, event, consensus, previous, major
            FROM econ_calendar WHERE date >= ? AND date <= ?
            ORDER BY date, major DESC, gmt
            LIMIT ?
            """,
            (today.isoformat(), (today + timedelta(days=days)).isoformat(), limit),
        ).fetchall()
    except Exception:
        return []
    out = []
    for r in rows:
        kst = ""
        if r["gmt"]:
            try:
                t = datetime.fromisoformat(f"{r['date']} {r['gmt']}") + timedelta(hours=9)
                kst = t.strftime("%H:%M") + ("+1" if t.date().isoformat() > r["date"] else "")
            except ValueError:
                pass
        dd = (date.fromisoformat(r["date"]) - today).days
        out.append({
            "date": r["date"],
            "dday": "오늘" if dd == 0 else "내일" if dd == 1 else f"{dd}일 후",
            "dd": dd, "kst": kst, "country": r["country"], "event": r["event"],
            "consensus": r["consensus"] or "–", "previous": r["previous"] or "–",
            "major": bool(r["major"]),
        })
    return out


def earnings_upcoming(con, days: int = 7, limit: int = 14) -> list[dict]:
    """향후 실적 일정 (US, 시총 큰 순 우선)."""
    from datetime import date, timedelta

    today = date.today()
    try:
        rows = con.execute(
            """
            SELECT e.symbol, e.date, e.when_time, e.name, e.eps_forecast, sm.mcap
            FROM earnings_calendar e
            LEFT JOIN stock_meta sm ON sm.symbol = e.symbol
            WHERE e.date >= ? AND e.date <= ?
            ORDER BY e.date, sm.mcap DESC
            LIMIT ?
            """,
            (today.isoformat(), (today + timedelta(days=days)).isoformat(), limit),
        ).fetchall()
    except Exception:
        return []
    out = []
    for r in rows:
        dd = (date.fromisoformat(r["date"]) - today).days
        out.append({
            "symbol": r["symbol"], "name": r["name"], "date": r["date"],
            "dday": "오늘" if dd == 0 else "내일" if dd == 1 else f"{dd}일 후",
            "dd": dd,
            "time_ko": EARN_TIME_KO.get(r["when_time"], "미정"),
            "eps": r["eps_forecast"] or "–",
            "mcap_fmt": fmt_usd(r["mcap"]) if r["mcap"] else "–",
        })
    return out
