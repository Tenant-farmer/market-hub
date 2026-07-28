"""주간 경제 일정 다이제스트 — 이번 주 무엇이 시장을 흔드나 (KST 기준).

사용자 제시 포맷(2026-07-29): 요일별로 묶고 각 요일을 **접을 수 있게**.
매일 오는 시황·상태 리포트와 달리 **주 1회(월요일 아침)** 나가는 '이번 주 지도'다.

- 시각은 전부 **KST**. 원본 `gmt` 필드는 이름과 달리 **ET**라 ET→KST로 변환한다
  (src/timeutil — +9h로 잘못 보던 걸 2026-07-29에 바로잡음). 날짜가 넘어가면
  실제 KST 날짜의 요일 블록으로 옮긴다 — 그래야 '한국시간 기준' 표기가 정직하다
- major=1 인 것만. 전부 담으면 하루 70건이라 지도가 아니라 목록이 된다
- 마무리 코멘트는 **데이터에서 뽑는다**(금리결정이 몇 건, 어느 나라) — 추측 문장 금지

수동: python -m src.jobs.econ_week [--dry]
"""
from datetime import date, datetime, timedelta

WD = "월화수목금토일"
FLAG = {"US": "🇺🇸", "KR": "🇰🇷", "JP": "🇯🇵", "CN": "🇨🇳",
        "EU": "🇪🇺", "DE": "🇩🇪", "UK": "🇬🇧"}
RATE_KW = ("Interest Rate", "Rate Decision", "FOMC", "Monetary Policy", "BOJ", "ECB", "BoE")

# major 안에서도 무게가 다르다 — 금리결정·CPI·고용·GDP는 시장을 직접 움직이고
# 소비자심리·무역수지는 참고에 가깝다. 앞의 것만 굵게(2026-07-29 사용자 요청).
KEY_KW = RATE_KW + ("CPI", "PCE", "Nonfarm", "Payroll", "Unemployment", "GDP",
                    "ISM", "Retail Sales")


# GDPNow는 애틀랜타 연준의 **실시간 추정치**라 발표 이벤트가 아니다(매주 여러 번 갱신) —
# 'GDP'에 걸려 굵게 나오던 걸 제외한다
KEY_EXCLUDE = ("GDPNow", "forecast")


def _is_key(event: str) -> bool:
    if any(k.lower() in event.lower() for k in KEY_EXCLUDE):
        return False
    return any(k.lower() in event.lower() for k in KEY_KW)

# 수집은 넓게, 표시는 좁게 — 넓힌 국가(DE·EU·UK)를 켜니 금요일에만 37건이 찍혔다.
# 독일은 **주(州)별 CPI**를 6개씩 따로 발표하고, BoE는 위원 투표수를 항목마다 나눈다.
# 지도가 목록이 되지 않게 이런 하위 항목을 뺀다(전체는 /econ 탭에서 볼 수 있다).
NOISE = ("Baden", "Bavaria", "Brandenburg", "Hesse", "Saxony", "North Rhine",
         "MPC vote", "Auction", "Bill ", "Bond ", "Letter", "Redbook", "API Weekly",
         "EIA ", "Cushing", "Rig Count", "Money Supply")
DAY_MAX = 12                    # 요일당 표시 상한 — 넘으면 '+N건 더'

# 접이식 블록의 폭은 **가장 긴 줄**이 정한다 → 요일마다 상자 크기가 달랐다(실측 19~45자).
# 폭 맞춤을 **별도 줄**로 넣었더니 일정 1건짜리 요일에서 빈 줄처럼 보였다(사용자 지적 2회).
# → 줄을 늘리지 말고 **헤더 끝에 가로로** 보이지 않는 문자를 채운다. 행 수는 그대로고
#    폭만 맞는다. U+2800(점자 공백)은 렌더링이 비어 있으면서 자리를 차지한다.
EVENT_MAX = 20                  # 이벤트명 표시 상한
PAD = "⠀"                  # 보이지 않는 폭 채움 문자
MIN_W = 30                      # 하한 — 내용이 짧은 주에도 너무 좁아지지 않게
# 목표 폭은 **고정값이 아니라 그 주의 실제 최장 줄에서 계산**한다.
# 34로 박아두면 지표 이름이 긴 주에 그 요일만 삐져나온다(2026-07-29 사용자 지적).
# _short()가 이름을 자르므로 이론상 32셀이 상한이지만, 그 보장이 암묵적이라 의존하지 않는다.


def _w(text: str) -> int:
    """대략적 표시 폭 — 한글·이모지 등 넓은 글자는 2셀로 센다."""
    return sum(2 if (ord(ch) > 0x1100 and ord(ch) != 0x2800) else 1 for ch in text)


def _is_noise(event: str) -> bool:
    return any(k.lower() in event.lower() for k in NOISE)


def _short(event: str) -> str:
    """이벤트명 절단 — 단어 경계 우선, 넘치면 말줄임."""
    e = event.strip()
    if len(e) <= EVENT_MAX:
        return e
    cut = e[:EVENT_MAX]
    i = cut.rfind(" ")
    return (cut[:i] if i > EVENT_MAX * 0.6 else cut).rstrip(" ,.") + "…"


def _kst(d: str, gmt: str):
    """(KST 날짜, HH:MM). gmt가 없으면 (원래 날짜, None) — 시각 미정 이벤트.

    `gmt` 필드는 이름과 달리 **ET**다(src/timeutil 참조). 여름 +13h·겨울 +14h.
    """
    from src.timeutil import et_to_kst

    t = et_to_kst(d, gmt)
    if t is None:
        return d, None
    return t.date().isoformat(), t.strftime("%H:%M")


def week_events(con, monday: date) -> dict:
    """{KST 날짜: [(HH:MM, 국가, 이벤트)]} — major만, 시각순."""
    lo = (monday - timedelta(days=1)).isoformat()      # KST 변환으로 하루 밀릴 수 있음
    hi = (monday + timedelta(days=7)).isoformat()
    try:
        rows = con.execute(
            "SELECT date, gmt, country, event FROM econ_calendar "
            "WHERE major=1 AND date BETWEEN ? AND ? ORDER BY date, gmt", (lo, hi)).fetchall()
    except Exception:
        return {}
    out: dict = {}
    seen = set()                                   # API가 같은 이벤트를 MoM/YoY로 두 번 준다
    for r in rows:
        if _is_noise(r["event"]):
            continue
        d, hm = _kst(r["date"], r["gmt"])
        if not (monday.isoformat() <= d <= (monday + timedelta(days=6)).isoformat()):
            continue
        key = (d, hm, r["country"], r["event"])
        if key in seen:
            continue
        seen.add(key)
        out.setdefault(d, []).append((hm or "시각미정", r["country"], r["event"]))
    for d in out:
        out[d].sort(key=lambda x: (x[0] == "시각미정", x[0]))
    return out


def _closing(events: dict) -> str:
    """마무리 코멘트 — **데이터에서만** 뽑는다(추측 문장 금지)."""
    rates = [(d, c, e) for d, items in events.items() for (_, c, e) in items
             if any(k.lower() in e.lower() for k in RATE_KW)]
    if not rates:
        n = sum(len(v) for v in events.values())
        return f"이번 주 금리결정은 없고 주요 지표 {n}건입니다."
    who = []
    for _, c, _ in rates:
        if c not in who:
            who.append(c)
    names = {"US": "미국", "KR": "한국", "JP": "일본", "CN": "중국",
             "EU": "유로존", "DE": "독일", "UK": "영국"}
    return (f"이번 주는 {', '.join(names.get(c, c) for c in who)}의 통화정책 발표가 "
            f"{len(rates)}건 예정돼 있어 변동성이 커질 수 있습니다.")


def build_text(con, monday: date | None = None) -> str:
    today = date.today()
    monday = monday or (today - timedelta(days=today.weekday()))
    ev = week_events(con, monday)
    sun = monday + timedelta(days=6)
    L = [f"<b>📅 이번 주 중요 경제 일정</b> ({monday.month}/{monday.day}~{sun.month}/{sun.day} "
         f"· 한국시간 기준)", ""]
    if not ev:
        L.append("<i>수집된 주요 일정이 없습니다 (캘린더는 임박해야 채워집니다)</i>")
        return "\n".join(L)
    # 1단계: 블록 내용을 먼저 만들고 **그 주의 실제 최장 줄**을 잰다
    blocks = []
    for i in range(7):
        d = monday + timedelta(days=i)
        items = ev.get(d.isoformat())
        if not items:
            continue                                   # 일정 없는 요일은 아예 안 띄운다
        label = f"[{d.month}/{d.day} {WD[d.weekday()]}요일] {len(items)}건"
        shown = items[:DAY_MAX]
        # (표시용 HTML, 폭 계산용 원문) 쌍 — 태그가 폭에 섞이면 계산이 틀어진다
        lines = []
        for hm, c, e in shown:
            txt = f"{hm} {FLAG.get(c, '')} {_short(e)}"
            lines.append((f"<b>{txt}</b>" if _is_key(e) else txt, txt))
        if len(items) > DAY_MAX:
            more = f"… 외 {len(items) - DAY_MAX}건"
            lines.append((f"<i>{more}</i>", more))
        blocks.append((label, lines))
    target = max([MIN_W, *(_w(raw) for _, ls in blocks for _, raw in ls),
                  *(_w(lb) for lb, _ in blocks)])

    # 2단계: 모든 헤더를 그 폭까지 **가로로** 채운다 — 별도 줄로 넣으면 1건짜리 요일에서
    # 빈 줄처럼 보인다(2026-07-29 사용자 지적 2회 끝에 이 방식으로 정착)
    for label, lines in blocks:
        head = f"<b>{label}</b>" + PAD * max(0, target - _w(label))
        body = "\n".join(html for html, _raw in lines)
        L.append(f"<blockquote expandable>{head}\n{body}</blockquote>")
    L += ["", f"<i>{_closing(ev)}</i>"]
    return "\n".join(L)


def send_week(con) -> int:
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
        print(re.sub(r"</?[bi]>|</?blockquote[^>]*>", "", build_text(c)))
    else:
        send_week(c)
        print("발송 완료")
    c.close()
