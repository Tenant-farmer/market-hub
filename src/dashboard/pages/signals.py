"""지표 분석 (/signals) — 대표지수 + 공포지표 시계열 동기화 + 매수신호 음영.

- US: 글로벌 신호 (VIX≥30 또는 VIX≥20&VVIX≥95) — 개요 신호등과 동일
- KR: 신호 기준 토글 (?sig=) — vkospi(기본, 현행 매매규칙: VKOSPI≥30 & KOSPI 낙폭-5%)
  / global(글로벌 신호를 KR 지수 위에 겹침 — 비교용). 진행표·이력·지연연구 모두 선택 기준으로 재계산.
"""
from flask import Blueprint, render_template, request

from src import db

bp = Blueprint("signals", __name__)

START = "2007-01-01"    # 2008 금융위기 포함 (최악 케이스 관찰)
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
    sig_v = mkt == "kr" and request.args.get("sig", "vkospi") != "global"
    sigmode = "vkospi" if sig_v else "global"
    con = db.connect()
    idxs = [{"code": c, "name": n, "data": _close(con, c, m)} for c, m, n in IDX[mkt]]
    vix = _close(con, "^VIX", "US_INDEX")
    vvix = _close(con, "^VVIX", "MACRO")
    vk = _close(con, "VKOSPI", "KR_INDEX") if mkt == "kr" else []
    fng = _sent(con, "fear_greed")
    con.close()

    vvix_by = {d["time"]: d["value"] for d in vvix}
    vk_by = {d["time"]: d["value"] for d in vk}
    dd_by = {}
    if sig_v:
        # KR 현행 규칙: VKOSPI≥30 & KOSPI 52주(252일) 고점 대비 -5% 이하
        # 음영: 매수신호 빨강(s=3) / VKOSPI≥30인데 낙폭 미달(과열 변동성) 주황(s=1)
        from bisect import bisect_right as _br

        kd = [d["time"] for d in idxs[0]["data"]]
        kc = [d["value"] for d in idxs[0]["data"]]
        roll_by = {t: (c / max(kc[max(0, i - 251): i + 1]) - 1) * 100
                   for i, (t, c) in enumerate(zip(kd, kc))}
        marks = []
        for d in vk:
            t = d["time"]
            dv = roll_by.get(t)
            if dv is None:                             # 휴장 불일치 시 직전 KOSPI 기준
                j = _br(kd, t) - 1
                dv = roll_by[kd[j]] if j >= 0 else None
            dd_by[t] = dv
            hot = d["value"] >= 30
            g = hot and dv is not None and dv <= -5
            marks.append({"time": t, "value": 1 if hot else 0,
                          "s": 3 if g else 1 if hot else 0})
    else:
        # 글로벌: 음영 = 원인 지표의 라인색 — VIX만(≥20) 주황 / VVIX만(≥95) 보라 / 둘 다 빨강
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
    # 표의 지표 컬럼: 글로벌=VIX/VVIX, VKOSPI 모드=VKOSPI/고점比 낙폭
    ia_by, ib_by = (vk_by, dd_by) if sig_v else (vix_by, vvix_by)

    def _green(m):
        return m["s"] == 3 if sig_v else (vix_by.get(m["time"], 0) >= 30) or m["s"] == 3

    hist, p1, p2 = [], None, None
    for m in marks:                                    # 전체 이력 (페이지네이션으로 열람)
        t = m["time"]
        c1v, c2v = by1.get(t), by2.get(t)
        fg = fng_by.get(t)
        green = _green(m)
        row = {"date": t, "s": m["s"], "vix": ia_by.get(t), "vvix": ib_by.get(t),
               "fng": fg, "avoid": 1 if (fg or 0) >= 75 else 0, "sig": green,
               "close": c1v, "chg": (c1v / p1 - 1) * 100 if (c1v and p1) else None,
               "close2": c2v, "chg2": (c2v / p2 - 1) * 100 if (c2v and p2) else None,
               "f1": None, "f7": None, "f21": None, "f63": None, "fwd_run": None,
               "trough": None}
        if green:
            i = asof1(t)
            row["f1"], row["f7"], row["f21"], row["f63"] = (
                fwd1(i, 1), fwd1(i, 7), fwd1(i, 21), fwd1(i, 63))
            if row["f63"] is None and i is not None and c1_:
                row["fwd_run"] = ((c1_[-1] / c1_[i] - 1) * 100, len(c1_) - 1 - i)
            if i is not None:                          # 이후 126일 내 저점 (이력표와 동일)
                win = c1_[i: i + 127]
                if len(win) > 1 and win[0]:
                    lo_i = min(range(len(win)), key=lambda k: win[k])
                    row["trough"] = (lo_i, (win[lo_i] / win[0] - 1) * 100)
        p1, p2 = c1v or p1, c2v or p2
        hist.append(row)
    hist = hist[1:][::-1]                              # 최신순
    pages = max(1, -(-len(hist) // PER))
    page = min(page, pages)
    year_pages, seen_y = [], set()                     # 연도 바로가기 (해당 연도 첫 페이지)
    for idx, r in enumerate(hist):
        y = r["date"][:4]
        if y not in seen_y:
            seen_y.add(y)
            year_pages.append((y, idx // PER + 1))
    hist = hist[(page - 1) * PER: page * PER]
    cur_year = hist[0]["date"][:4] if hist else ""
    lo = max(1, page - 3)
    page_links = [(p, p == page) for p in range(lo, min(pages, lo + 6) + 1)]

    # 신호 에피소드 (15일 갭 기준) — 그날 샀으면 +21/+63일 수익
    greens = [(m["time"], ia_by.get(m["time"], 0), ib_by.get(m["time"]),
               _green(m), m["s"]) for m in marks]
    episodes = []
    for k, (t, v, w, g, s) in enumerate(greens):
        if not g or any(gg[3] for gg in greens[max(0, k - 15):k]):
            continue
        i1, i2 = asof1(t), asof2(t)
        ep = {"date": t, "vix": v, "vvix": w, "s": s,
              "a1": fwd1(i1, 1), "a7": fwd1(i1, 7), "a21": fwd1(i1, 21), "a63": fwd1(i1, 63),
              "b1": fwd2(i2, 1), "b7": fwd2(i2, 7), "b21": fwd2(i2, 21), "b63": fwd2(i2, 63),
              "run": None, "trough": None}
        if i1 is not None:                             # 이후 126일 내 저점 (타이밍 연구와 동일 창)
            win = c1_[i1: i1 + 127]
            if len(win) > 1 and win[0]:
                lo = min(range(len(win)), key=lambda k: win[k])
                ep["trough"] = (lo, (win[lo] / win[0] - 1) * 100)
        if ep["a63"] is None and i1 is not None and c1_:
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

    # 이력 연도 필터 (통계는 전체 표본 유지, 표시만 필터)
    ep_years = sorted({e["date"][:4] for e in episodes}, reverse=True)
    ep_year = request.args.get("ep_year", "")
    if ep_year in ep_years:
        episodes = [e for e in episodes if e["date"][:4] == ep_year]
    else:
        ep_year = ""

    pills = [("미국 (SPY·QQQ)", "us", mkt == "us"), ("한국 (코스피·코스닥)", "kr", mkt == "kr")]
    # 차트 패널 데이터: VKOSPI 모드는 패널2=VKOSPI, 패널3=KOSPI 고점比 낙폭
    if sig_v:
        panel_a = vk
        panel_b = [{"time": t, "value": dd_by[t]} for t in sorted(dd_by)
                   if dd_by[t] is not None]
    else:
        panel_a, panel_b = vix, vvix
    return render_template("signals.html", mkt=mkt, idxs=idxs, vix=panel_a, vvix=panel_b,
                           fng=fng, marks=marks, avoid=avoid, hist=hist,
                           page=page, pages=pages, page_links=page_links,
                           year_pages=year_pages, cur_year=cur_year,
                           ep_years=ep_years, ep_year=ep_year,
                           episodes=episodes, delay_stats=delay_stats, best_delay=best,
                           pills=pills, sigmode=sigmode, sigv=sig_v)
