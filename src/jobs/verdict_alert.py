"""판정 자동 실행·발송 — 조건이 차면 사람이 기억하지 않아도 나간다.

날짜를 적어두고 수동으로 돌리면 잊는다. 그리고 2차는 **날짜가 아니라 표본**이 조건이라
(에쿼티 20영업일) 8/18에 찰 수도 8/22에 찰 수도 있다 → 조건 충족 시 자동 발송한다.

- 1차(시스템 안정성): VERDICT1_DATE 도달 시 1회
- 2차(전략 성과): 에쿼티 표본이 방향 판정 최소치(20일)에 도달 시 1회
멱등: collector_runs('verdict1'/'verdict2')로 재발송 방지. hourly 아침 슬롯에서 호출.

수동: python -m src.jobs.verdict_alert [--dry] [--force]
"""
import html
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))      # verdict.py / _perf_verdict.py 임포트용

VERDICT1_DATE = date(2026, 8, 6)
# 2차는 표본 조건(거래일 20일)만 있었는데, 실계좌 표본이 8/20에 겨우 20일이라
# 여유가 없었다 → 사용자 결정으로 **8/25 이후**로 미룬다(2026-07-28).
# 표본과 날짜를 **둘 다** 만족해야 발송한다 — 날짜만 넘고 표본이 모자라면 의미가 없고,
# 표본만 차고 날짜가 이르면 관찰 기간이 짧다.
VERDICT2_MIN_DATE = date(2026, 8, 25)


def _esc(v) -> str:
    """판정 항목·실측값을 HTML에 넣기 전 이스케이프.

    2026-07-28 실측: '수집 에러율 < 5%'의 `<`가 태그 시작으로 해석돼 텔레그램이 400을 냈다
    (`can't parse entities: Unsupported start tag`). 8/6 판정이 그대로 실패했을 것.
    """
    return html.escape(str(v), quote=False)


def _sent(con, kind: str) -> bool:
    return con.execute(
        "SELECT 1 FROM collector_runs WHERE collector=? AND status='ok' LIMIT 1",
        (kind,)).fetchone() is not None


def _mark(con, kind: str, msg: str) -> None:
    from datetime import datetime

    con.execute(
        "INSERT INTO collector_runs (collector, run_at, status, rows, message) "
        "VALUES (?,?, 'ok', 0, ?)",
        (kind, datetime.now().isoformat(timespec="seconds"), msg[:200]))
    con.commit()


def _eq_days(con) -> int:
    """에쿼티 표본 — **거래일만** 센다.

    portfolio_snapshots는 매시간 돌아 주말·공휴일에도 행이 생기는데, 장이 안 열린 날은
    금요일 값이 그대로 복사돼 **수익률 0%인 가짜 관측**이 된다(2026-07-25·26 실측).
    달력일로 세면 20일이 8/11에 차서 판정이 조기 발송되고, 더 나쁜 건 0% 날이
    변동성을 낮춰 **α의 t값을 부풀린다** — 판정이 스스로를 속이게 된다.
    strftime('%w')는 0=일, 6=토.
    """
    return con.execute(
        "SELECT COUNT(DISTINCT date) n FROM portfolio_snapshots "
        "WHERE CAST(strftime('%w', date) AS INTEGER) BETWEEN 1 AND 5").fetchone()["n"]


def build_first(con, since: str) -> str:
    from verdict import system_verdict

    rows = system_verdict(con, since)
    ok = all(r["ok"] for r in rows)
    L = [f"<b>{'✅' if ok else '❌'} 1차 판정 — 시스템 안정성</b>",
         f"<i>{since} ~ {date.today().isoformat()}</i>", ""]
    for r in rows:
        L.append(f"{'✅' if r['ok'] else '❌'} {_esc(r['item'])}")
        L.append(f"   <i>{_esc(r['detail'])}</i>")
    L += ["", f"<b>→ {'합격 — VPS 이전 진행 가능' if ok else '불합격 — 위 항목 해소 필요'}</b>"]
    return "\n".join(L)


def build_second(con, since: str) -> str:
    from _perf_verdict import perf_verdict

    rows = perf_verdict(con, since)
    judged = [r for r in rows if r["ok"] is not None]
    bad = [r for r in judged if not r["ok"]]
    L = [f"<b>📊 2차 판정 — 전략 성과</b> (표본 {_eq_days(con)}일)",
         "<i>백테스트 예측이 실거동에서 재현되는가</i>", ""]
    for r in rows:
        icon = {True: "✅", False: "❌", None: "⏳"}[r["ok"]]
        L.append(f"{icon} {_esc(r['item'])}")
        L.append(f"   <i>{_esc(r['detail'])}</i>")
    L.append("")
    if not judged:
        L.append("<b>→ 판정 보류 — 표본 부족</b>")
    elif bad:
        L.append(f"<b>→ 예측 빗나감 {len(bad)}건</b>: "
                 + ", ".join(_esc(r["item"]) for r in bad))
    else:
        L.append(f"<b>→ 판정 {len(judged)}항목 전부 예측대로</b>")
    return "\n".join(L)


def run(con, dry: bool = False, force: bool = False) -> int:
    """조건 충족한 판정을 발송. 반환: 발송 건수."""
    from src import notify
    from _perf_verdict import MIN_DIRECTION_DAYS

    since = "2026-07-23"
    sent = 0
    jobs = [
        ("verdict1", date.today() >= VERDICT1_DATE, lambda: build_first(con, since)),
        ("verdict2", _eq_days(con) >= MIN_DIRECTION_DAYS and date.today() >= VERDICT2_MIN_DATE,
         lambda: build_second(con, since)),
    ]
    for kind, due, build in jobs:
        if not force and (not due or _sent(con, kind)):
            continue
        text = build()
        if dry:
            print(f"--- {kind} ---\n{text}\n")
        else:
            notify.send(text)
            _mark(con, kind, f"{kind} 발송")
        sent += 1
    return sent


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    sys.path.insert(0, str(ROOT))
    from src import db

    c = db.connect()
    n = run(c, dry="--dry" in sys.argv, force="--force" in sys.argv)
    print(f"{n}건 " + ("미리보기" if "--dry" in sys.argv else "발송"))
    c.close()
