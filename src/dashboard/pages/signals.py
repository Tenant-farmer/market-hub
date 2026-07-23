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

    # ---- 하단 진행 표 (두 지수 + 기간 선택) & 매수신호 이력 (전 기간 에피소드 + 진입지연 연구) ----
    from bisect import bisect_right

    PER = 30
    try:
        page = max(1, int(request.args.get("page", 1)))
    except ValueError:
        page = 1

    def _tools(data):
        ds = [d["time"] for d in data]
        cs = [d["value"] for d in data]

        def asof(t):
            i = bisect_right(ds, t) - 1
            return i if i >= 0 else None

        def fwd(i, n):
            return ((cs[i + n] / cs[i] - 1) * 100
                    if i is not None and cs[i] and i + n < len(cs) else None)

        return ds, cs, asof, fwd

    d1_, c1_, asof1, fwd1 = _tools(idxs[0]["data"] if idxs else [])
    d2_, c2_, asof2, fwd2 = _tools(idxs[1]["data"] if len(idxs) > 1 else [])
    vix_by = {d["time"]: d["value"] for d in vix}
    fng_by = {d["time"]: d["value"] for d in fng}
    by1 = {t: c for t, c in zip(d1_, c1_)}
    by2 = {t: c for t, c in zip(d2_, c2_)}

    hist, p1, p2 = [], None, None
    for m in marks:                                    # 전체 이력 (페이지네이션으로 열람)
        t = m["time"]
        c1v, c2v = by1.get(t), by2.get(t)
        fg = fng_by.get(t)
        green = (vix_by.get(t, 0) >= 30) or m["s"] == 3
        row = {"date": t, "s": m["s"], "vix": vix_by.get(t), "vvix": vvix_by.get(t),
               "fng": fg, "avoid": 1 if (fg or 0) >= 75 else 0, "sig": green,
               "close": c1v, "chg": (c1v / p1 - 1) * 100 if (c1v and p1) else None,
               "close2": c2v, "chg2": (c2v / p2 - 1) * 100 if (c2v and p2) else None,
               "fwd": None, "fwd_run": None}
        if green:
            i = asof1(t)
            row["fwd"] = fwd1(i, 63)
            if row["fwd"] is None and i is not None and c1_:
                row["fwd_run"] = ((c1_[-1] / c1_[i] - 1) * 100, len(c1_) - 1 - i)
        p1, p2 = c1v or p1, c2v or p2
        hist.append(row)
    hist = hist[1:][::-1]                              # 최신순
    pages = max(1, -(-len(hist) // PER))
    page = min(page, pages)
    hist = hist[(page - 1) * PER: page * PER]
    lo = max(1, page - 3)
    page_links = [(p, p == page) for p in range(lo, min(pages, lo + 6) + 1)]

    # 신호 에피소드 (15일 갭 기준) — 그날 샀으면 +21/+63일 수익
    greens = [(m["time"], vix_by.get(m["time"], 0), vvix_by.get(m["time"]),
               (vix_by.get(m["time"], 0) >= 30) or m["s"] == 3, m["s"]) for m in marks]
    episodes = []
    for k, (t, v, w, g, s) in enumerate(greens):
        if not g or any(gg[3] for gg in greens[max(0, k - 15):k]):
            continue
        i1, i2 = asof1(t), asof2(t)
        ep = {"date": t, "vix": v, "vvix": w, "s": s,
              "f21": fwd1(i1, 21), "f63": fwd1(i1, 63), "f63b": fwd2(i2, 63), "run": None}
        if ep["f63"] is None and i1 is not None and c1_:
            ep["run"] = ((c1_[-1] / c1_[i1] - 1) * 100, len(c1_) - 1 - i1)
        episodes.append(ep)
    episodes = episodes[::-1]

    # 진입지연 연구 — 신호 후 며칠에 사야 하나 (63일 보유, 본지수 기준 실계산)
    delay_stats = []
    starts = [asof1(e["date"]) for e in episodes if asof1(e["date"]) is not None]
    for dly in (0, 3, 5, 10, 21):
        rets = [ (c1_[i + dly + 63] / c1_[i + dly] - 1) * 100
                 for i in starts if i + dly + 63 < len(c1_) ]
        if len(rets) >= 5:
            rets.sort()
            delay_stats.append({"delay": dly, "n": len(rets),
                                "win": sum(1 for r in rets if r > 0) / len(rets) * 100,
                                "med": rets[len(rets) // 2]})
    best = max(delay_stats, key=lambda s: s["win"])["delay"] if delay_stats else None

    pills = [("미국 (SPY·QQQ)", "us", mkt == "us"), ("한국 (코스피·코스닥)", "kr", mkt == "kr")]
    return render_template("signals.html", mkt=mkt, idxs=idxs, vix=vix, vvix=vvix,
                           fng=fng, marks=marks, avoid=avoid, hist=hist,
                           page=page, pages=pages, page_links=page_links,
                           episodes=episodes, delay_stats=delay_stats, best_delay=best,
                           pills=pills)
