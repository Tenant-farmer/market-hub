"""지표·실적 발표 즉시 알림 — 워커가 5분마다 점검, 발표 확인 시 텔레그램.

- 경제지표(US·KR major): 발표시각(gmt+9h=KST) 도달 → Nasdaq API 당일 재조회로 actual 확인
  → "📊 CPI 발표: 3.2% (예상 3.1% · 이전 3.4%)". actual이 아직 비면 다음 사이클 재확인,
  30분 넘게 비면 예상치만으로 1회 알림(값 대기 표기), 2시간 지나면 조용히 종료
- 실적(감시 = 로테이션 US 슬롯 + AAPL): 발표 시간대 도달 시 알림 —
  장전(BMO) = 당일 19:00 KST(=06:00 ET), 장후(AMC·미표기) = 익일 05:00 KST(=16:00 ET)
- 멱등: collector_runs('event_alert', message=키) — 이벤트당 1회
"""
from datetime import datetime, timedelta

import requests

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Accept": "application/json"}
ECON_URL = "https://api.nasdaq.com/api/calendar/economicevents"
FLAG = {"US": "🇺🇸", "KR": "🇰🇷"}


def _once(con, key: str) -> bool:
    """key 최초면 기록 후 True (이벤트당 1회 발신 보장)."""
    dup = con.execute("SELECT 1 FROM collector_runs WHERE collector='event_alert' "
                      "AND message=? LIMIT 1", (key,)).fetchone()
    if dup:
        return False
    con.execute("INSERT INTO collector_runs (collector, run_at, status, rows, message) "
                "VALUES ('event_alert', ?, 'ok', 0, ?)",
                (datetime.now().isoformat(timespec="seconds"), key))
    con.commit()
    return True


def _send(text: str):
    try:
        from src import notify

        notify.send(text)
    except Exception:
        pass


def _refresh_econ(con, d: str) -> None:
    """해당 날짜의 actual 값을 API에서 갱신 (1콜로 그날 전체)."""
    try:
        r = requests.get(ECON_URL, params={"date": d}, headers=UA, timeout=20)
        for row in (r.json().get("data") or {}).get("rows") or []:
            act = (row.get("actual") or "").strip()
            if act:
                con.execute("UPDATE econ_calendar SET actual=? WHERE date=? AND event=?",
                            (act, d, (row.get("eventName") or "").strip()))
        con.commit()
    except Exception:
        pass


def check(con, now: datetime | None = None) -> int:
    """발표 점검 — 보낸 알림 수 반환. now 주입은 테스트용."""
    now = now or datetime.now()
    n = 0

    # ---- 경제지표 (major) — 발표시각 지난 것 중 미알림 ----
    try:
        rows = con.execute(
            "SELECT date, gmt, country, event, actual, consensus, previous FROM econ_calendar "
            "WHERE major=1 AND gmt != '' AND date >= ?",
            ((now - timedelta(days=1)).date().isoformat(),)).fetchall()
    except Exception:
        rows = []
    refreshed = set()
    for r in rows:
        try:
            rel = datetime.fromisoformat(f"{r['date']} {r['gmt']}") + timedelta(hours=9)
        except ValueError:
            continue
        age = (now - rel).total_seconds()
        if age < 0 or age > 7200:                      # 아직 전 / 2시간 경과 → 스킵
            continue
        key = f"econ_{r['date']}_{r['event']}"
        if con.execute("SELECT 1 FROM collector_runs WHERE collector='event_alert' "
                       "AND message=? LIMIT 1", (key,)).fetchone():
            continue
        actual = (r["actual"] or "").strip()
        if not actual and r["date"] not in refreshed:  # 값 재조회 (날짜당 1콜/사이클)
            _refresh_econ(con, r["date"])
            refreshed.add(r["date"])
            r2 = con.execute("SELECT actual FROM econ_calendar WHERE date=? AND event=?",
                             (r["date"], r["event"])).fetchone()
            actual = (r2["actual"] or "").strip() if r2 else ""
        if not actual and age < 1800:                  # 30분까진 값 기다림
            continue
        if _once(con, key):
            val = (f"실제 <b>{actual}</b>" if actual else "값 대기 중")
            tail = " · ".join(x for x in (
                f"예상 {r['consensus']}" if r["consensus"] else "",
                f"이전 {r['previous']}" if r["previous"] else "") if x)
            _send(f"📊 {FLAG.get(r['country'], '')} <b>{r['event']}</b> 발표 — {val}"
                  + (f" ({tail})" if tail else ""))
            n += 1

    # ---- 실적 (감시: 로테이션 US + AAPL) — 발표 시간대 도달 ----
    try:
        watch = {str(x["symbol"]) for x in con.execute("SELECT symbol FROM rotation_slots")
                 if not str(x["symbol"]).isdigit()} | {"AAPL"}
        ers = con.execute(
            "SELECT symbol, date, when_time, name, eps_forecast FROM earnings_calendar "
            "WHERE date >= ? AND date <= ?",
            ((now - timedelta(days=1)).date().isoformat(), now.date().isoformat())).fetchall()
    except Exception:
        ers = []
    for e in ers:
        if e["symbol"] not in watch:
            continue
        pre = "pre" in (e["when_time"] or "")
        d = datetime.fromisoformat(e["date"])
        trig = d.replace(hour=19) if pre else (d + timedelta(days=1)).replace(hour=5)
        if not (trig <= now <= trig + timedelta(hours=6)):
            continue
        key = f"earn_{e['date']}_{e['symbol']}"
        if _once(con, key):
            eps = f" — 예상 EPS {e['eps_forecast']}" if e["eps_forecast"] else ""
            _send(f"📈 <b>{e['symbol']}</b> 실적 발표 시간대 ({'장전' if pre else '장 마감 후'})"
                  f"{eps} · {e['name'][:30]}")
            n += 1
    return n


if __name__ == "__main__":
    import sys

    from dotenv import load_dotenv

    load_dotenv()
    sys.path.insert(0, ".")
    from src import db

    c = db.connect()
    print("알림:", check(c))
    c.close()
