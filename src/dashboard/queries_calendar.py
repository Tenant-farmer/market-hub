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


# ---------------------------------------------------------------- 실적 캘린더 그리드
# Earnings Whispers 스타일: 주간은 요일×발표시점 격자, 월간은 달력 격자.
# 리스트 뷰는 "다음에 뭐가 오나"를, 격자 뷰는 "이번 주 어느 날이 무거운가"를 보여준다.
SLOTS = [("time-pre-market", "🌅 장전"), ("time-after-hours", "🌙 장마감 후"),
         ("time-not-supplied", "· 미정")]
WEEK_CELL_MAX = 20          # 셀당 표시 상한 (성수기엔 하루 90건까지 나온다)


def _earn_rows(con, start, end) -> list:
    """[start, end] 구간 실적 일정 — 시총 큰 순."""
    try:
        return con.execute(
            "SELECT e.symbol, e.date, e.when_time, e.name, e.eps_forecast, sm.mcap "
            "FROM earnings_calendar e LEFT JOIN stock_meta sm ON sm.symbol = e.symbol "
            "WHERE e.date BETWEEN ? AND ? ORDER BY sm.mcap DESC",
            (start.isoformat(), end.isoformat())).fetchall()
    except Exception:
        return []


def _cell(r, today):
    return {"symbol": r["symbol"], "name": (r["name"] or "")[:28],
            "eps": r["eps_forecast"] or "–",
            "mcap": r["mcap"] or 0, "mcap_fmt": fmt_usd(r["mcap"]) if r["mcap"] else "",
            "big": bool(r["mcap"] and r["mcap"] >= 2e11),      # 2000억 달러↑ = 대형
            "past": r["date"] < today.isoformat()}


def earnings_week(con, offset: int = 0) -> dict:
    """주간 격자 — 월~금 × (장전·장마감후·미정). offset: 0=이번주, -1=지난주."""
    from datetime import date, timedelta

    today = date.today()
    mon = today - timedelta(days=today.weekday()) + timedelta(weeks=offset)
    days = [mon + timedelta(days=i) for i in range(5)]
    rows = _earn_rows(con, days[0], days[-1])

    grid = {s: {d.isoformat(): [] for d in days} for s, _ in SLOTS}
    for r in rows:
        slot = r["when_time"] if r["when_time"] in grid else "time-not-supplied"
        if r["date"] in grid[slot]:
            grid[slot][r["date"]].append(_cell(r, today))
    return {
        "kind": "week", "offset": offset,
        "label": f"{mon.month}/{mon.day} ~ {days[-1].month}/{days[-1].day}"
                 + (" (이번 주)" if offset == 0 else ""),
        "days": [{"date": d.isoformat(), "md": f"{d.month}/{d.day}",
                  "dow": "월화수목금"[i], "today": d == today} for i, d in enumerate(days)],
        # 셀당 상위 시총 WEEK_CELL_MAX개만 — 하루 90개가 찍히면 격자가 아니라 벽이 된다
        "slots": [{"key": k, "label": lab,
                   "cells": [{"syms": grid[k][d.isoformat()][:WEEK_CELL_MAX],
                              "more": max(0, len(grid[k][d.isoformat()]) - WEEK_CELL_MAX)}
                             for d in days]}
                  for k, lab in SLOTS],
        "total": len(rows),
    }


def earnings_month(con, offset: int = 0) -> dict:
    """월간 격자 — 주×월~금. 셀마다 시총 상위 몇 개 + 총 건수."""
    from calendar import monthrange
    from datetime import date, timedelta

    today = date.today()
    y, m = today.year, today.month + offset
    y, m = y + (m - 1) // 12, (m - 1) % 12 + 1
    first, last = date(y, m, 1), date(y, m, monthrange(y, m)[1])
    rows = _earn_rows(con, first - timedelta(days=7), last + timedelta(days=7))

    by_day: dict[str, list] = {}
    for r in rows:
        by_day.setdefault(r["date"], []).append(_cell(r, today))

    weeks, cur = [], first - timedelta(days=first.weekday())   # 그 달 첫 주의 월요일
    while cur <= last:
        week = []
        for i in range(5):
            d = cur + timedelta(days=i)
            items = by_day.get(d.isoformat(), [])
            week.append({"date": d.isoformat(), "day": d.day, "in_month": d.month == m,
                         "today": d == today, "n": len(items), "syms": items[:4]})
        weeks.append(week)
        cur += timedelta(days=7)
    return {
        "kind": "month", "offset": offset,
        "label": f"{y}년 {m}월" + (" (이번 달)" if offset == 0 else ""),
        "dows": list("월화수목금"), "weeks": weeks,
        "total": sum(len(v) for k, v in by_day.items() if k[:7] == f"{y}-{m:02d}"),
    }
