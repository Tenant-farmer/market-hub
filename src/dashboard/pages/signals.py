"""지표 분석 (/signals) — 대표지수 + 공포지표(VIX·VVIX·F&G) 시계열 동기화 + 매수신호 음영.

공포지표는 미국(글로벌) 지표. 한국은 글로벌 공포에 베타로 움직이므로 같은 신호를 KR 지수 위에 겹침.
매수신호 green ⟺ VIX≥30 또는 (VIX≥20 & VVIX≥95) — 개요 신호등과 동일 정의.
"""
from flask import Blueprint, render_template, request

from src import db

bp = Blueprint("signals", __name__)

START = "2015-01-01"
IDX = {  # (symbol, market, 표시명)
    "us": [("SPY", "US", "SPY"), ("QQQ", "US", "QQQ")],
    "kr": [("1001", "KR_INDEX", "코스피"), ("2001", "KR_INDEX", "코스닥")],
}


def _close(con, symbol, market):
    return [{"time": r["date"], "value": r["close"]} for r in con.execute(
        "SELECT date, close FROM prices_daily WHERE symbol=? AND market=? AND date>=? "
        "ORDER BY date", (symbol, market, START)) if r["close"] is not None]


def _sent(con, metric):
    return [{"time": r["date"], "value": r["value"]} for r in con.execute(
        "SELECT date, value FROM sentiment_daily WHERE metric=? AND date>=? ORDER BY date",
        (metric, START))]


@bp.get("/signals")
def signals():
    mkt = request.args.get("mkt", "us")
    if mkt not in ("us", "kr"):
        mkt = "us"
    con = db.connect()
    idxs = [{"code": c, "name": n, "data": _close(con, c, m)} for c, m, n in IDX[mkt]]
    vix = _close(con, "^VIX", "US_INDEX")
    vvix = _close(con, "^VVIX", "MACRO")
    fng = _sent(con, "fear_greed")
    con.close()

    # 음영 = 원인 지표의 라인색: VIX만(≥20) 주황 / VVIX만(≥95) 보라 / 둘 다 빨강 (s: 1/2/3)
    vvix_by = {d["time"]: d["value"] for d in vvix}
    marks = []
    for d in vix:
        a = d["value"] >= 20
        w = vvix_by.get(d["time"])
        b = w is not None and w >= 95
        marks.append({"time": d["time"], "value": 1 if (a or b) else 0,
                      "s": 3 if (a and b) else 1 if a else 2 if b else 0})
    avoid = [{"time": d["time"], "value": 1 if d["value"] >= 75 else 0} for d in fng]

    pills = [("미국 (SPY·QQQ)", "us", mkt == "us"), ("한국 (코스피·코스닥)", "kr", mkt == "kr")]
    return render_template("signals.html", mkt=mkt, idxs=idxs, vix=vix, vvix=vvix,
                           fng=fng, marks=marks, avoid=avoid, pills=pills)
