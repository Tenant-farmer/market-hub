"""대시보드 페이지 공용 조회."""
from src.analytics import store
from src.dashboard.fmt import QUAD_DESC

RANKING_ALIASES = {
    "score": "leader_score",
    "ret21": "ret_21",
    "rs21": "rs_21",
    "rs63": "rs_63",
    "quad": "quadrant",
    "streak": "lead_streak",
    "rsi": "rsi",
    "hot": "overheat",
}


def ranking(con, scope: str):
    """섹터 랭킹 (주도점수순). 반환: (기준일, rows)."""
    return store.pivot_latest(
        con, scope, RANKING_ALIASES,
        date=store.latest_date(con, scope, "rs_21"),
        order_by="(score IS NULL), score DESC",
    )


def leader_cards(ranking_rows: list[dict], names: dict, top: int = 3) -> list[dict]:
    """주도 점수 상위 top개를 근거 설명과 함께 카드 데이터로."""
    cards = []
    for r in ranking_rows:
        if r["score"] is None:
            continue
        reasons = []
        if r["rs21"] is not None:
            reasons.append(f"1개월 RS {r['rs21']:+.1f}%p")
        if r["rs63"] is not None:
            reasons.append(f"3개월 RS {r['rs63']:+.1f}%p")
        if r["quad"]:
            q = QUAD_DESC[int(r["quad"])]
            if int(r["quad"]) == 1 and r["streak"]:
                n = int(r["streak"])
                q += f" {n}일째" + (" (지속 구간)" if n >= 21 else " (검증 중)")
            reasons.append(q)
        cards.append({
            "code": r["code"],
            "name": names.get(r["code"], ""),
            "score": r["score"],
            "reason": " · ".join(reasons),
            "hot": bool(r["hot"]),
        })
        if len(cards) == top:
            break
    return cards


def trails(con, scope: str, points: int = 12, step: int = 5):
    """RRG 궤적: 심볼별 (date, rs_ratio, rs_mom) 시퀀스를 step 간격으로 샘플."""
    rows = con.execute(
        "SELECT date, code, metric, value FROM analytics_daily "
        "WHERE scope=? AND metric IN ('rs_ratio','rs_mom') ORDER BY date",
        (scope,),
    ).fetchall()
    by: dict[str, dict[str, dict[str, float]]] = {}
    for r in rows:
        by.setdefault(r["code"], {}).setdefault(r["date"], {})[r["metric"]] = r["value"]
    out = {}
    for code, dates in by.items():
        seq = [
            [d, v["rs_ratio"], v["rs_mom"]]
            for d, v in sorted(dates.items())
            if "rs_ratio" in v and "rs_mom" in v
        ]
        tail = seq[-(points * step):]
        samp = tail[::step]
        if samp and samp[-1] != seq[-1]:
            samp.append(seq[-1])
        out[code] = samp
    return out


def prices(con, sym: str, n: int = 260):
    rows = con.execute(
        "SELECT date, close FROM prices_daily WHERE symbol=? ORDER BY date DESC LIMIT ?",
        (sym, n),
    ).fetchall()
    return [{"time": r["date"], "value": round(r["close"], 2)} for r in reversed(rows)]
