"""대시보드 페이지 공용 조회."""
from src import config
from src.analytics import store
from src.dashboard.fmt import INV_KO, QUAD_DESC, fmt_krw, fmt_usd

RANKING_ALIASES = {
    "score": "leader_score",
    "ret21": "ret_21",
    "rs21": "rs_21",
    "rs63": "rs_63",
    "quad": "quadrant",
    "streak": "lead_streak",
    "vshare": "val_share_ratio",
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


def ohlcv(con, sym: str, n: int = 260):
    """캔들차트용 OHLCV. Lightweight Charts 형식."""
    rows = con.execute(
        "SELECT date, open, high, low, close, volume FROM prices_daily "
        "WHERE symbol=? AND open IS NOT NULL AND close IS NOT NULL "
        "ORDER BY date DESC LIMIT ?",
        (sym, n),
    ).fetchall()
    return [
        {
            "time": r["date"],
            "open": round(r["open"], 2), "high": round(r["high"], 2),
            "low": round(r["low"], 2), "close": round(r["close"], 2),
            "volume": r["volume"] or 0,
        }
        for r in reversed(rows)
    ]


def prices(con, sym: str, n: int = 260):
    rows = con.execute(
        "SELECT date, close FROM prices_daily WHERE symbol=? ORDER BY date DESC LIMIT ?",
        (sym, n),
    ).fetchall()
    return [{"time": r["date"], "value": round(r["close"], 2)} for r in reversed(rows)]


CAP_BUCKETS = {
    "kr": [
        ("all", "전체", None, None), ("b1", "10조 이상", 1e13, None),
        ("b2", "1조-10조", 1e12, 1e13), ("b3", "1천억-1조", 1e11, 1e12),
        ("b4", "1천억 미만", None, 1e11),
    ],
    "us": [
        ("all", "전체", None, None), ("b1", "$200B 이상", 2e11, None),
        ("b2", "$10B-$200B", 1e10, 2e11), ("b3", "$2B-$10B", 2e9, 1e10),
        ("b4", "$300M-$2B", 3e8, 2e9),
    ],
}


def stock_hub(con, mkt="kr", cap="all", sector=None, sort="mcap", q=None, page=1, per=50):
    """종목 허브 — 시장/시총구간/섹터 필터 + 검색 + 시총·거래량 정렬 (최신 거래일 기준)."""
    sm = "KR" if mkt == "kr" else "US_STOCK"
    d0row = con.execute(
        "SELECT MAX(date) mx FROM prices_daily WHERE market=?", (sm,)
    ).fetchone()
    if not d0row or not d0row["mx"]:
        return None
    d0 = d0row["mx"]
    d1 = con.execute(
        "SELECT MAX(date) mx FROM prices_daily WHERE market=? AND date<?", (sm, d0)
    ).fetchone()["mx"]

    where, args = ["m.market=?", "s.mcap IS NOT NULL"], [sm]
    rng = next((b for b in CAP_BUCKETS[mkt] if b[0] == cap), None)
    if rng:
        if rng[2] is not None:
            where.append("s.mcap >= ?")
            args.append(rng[2])
        if rng[3] is not None:
            where.append("s.mcap < ?")
            args.append(rng[3])
    if sector:
        where.append("m.sector_name = ?")
        args.append(sector)
    if q:
        where.append("(m.name LIKE ? OR m.stock_code LIKE ?)")
        args += [f"%{q}%", f"{q}%"]

    body = f"""
        FROM sector_map m
        JOIN stock_meta s ON s.symbol = m.stock_code
        JOIN prices_daily p ON p.symbol = m.stock_code AND p.date = ?
        LEFT JOIN prices_daily pp ON pp.symbol = m.stock_code AND pp.date = ?
        WHERE {" AND ".join(where)}
    """
    qargs = [d0, d1, *args]
    total = con.execute(f"SELECT COUNT(*) n {body}", qargs).fetchone()["n"]
    order = "p.volume DESC" if sort == "vol" else "s.mcap DESC"
    rows = con.execute(
        f"SELECT m.stock_code code, m.name, m.sector_name sector, s.mcap, "
        f"p.close, p.volume, pp.close prev {body} ORDER BY {order} LIMIT ? OFFSET ?",
        [*qargs, per, (page - 1) * per],
    ).fetchall()
    out = []
    for r in rows:
        out.append({
            "code": r["code"], "name": r["name"] or r["code"], "sector": r["sector"] or "",
            "mcap": r["mcap"], "close": r["close"], "volume": r["volume"],
            "chg": round((r["close"] / r["prev"] - 1) * 100, 2) if r["prev"] else None,
        })
    sectors = [
        r["sector_name"] for r in con.execute(
            "SELECT DISTINCT sector_name FROM sector_map "
            "WHERE market=? AND sector_name IS NOT NULL ORDER BY sector_name", (sm,)
        )
    ]
    return {
        "rows": out, "total": total, "sectors": sectors, "date": d0,
        "pages": max(1, -(-total // per)),
    }


def us_capex(con):
    """US 섹터 CapEx 요약."""
    return _capex_summary(con, "us_capex")


def kr_capex(con):
    """KR 업종 CapEx 요약 — 1위 종목은 종목명으로 표시."""
    res = _capex_summary(con, "kr_capex")
    if res:
        for r in res["rows"]:
            row = con.execute(
                "SELECT name FROM sector_map WHERE stock_code=? AND market='KR'", (r["top"],)
            ).fetchone()
            if row and row["name"]:
                r["top"] = row["name"]
    return res


def _capex_summary(con, table: str):
    """섹터별 CapEx — 시총 상위 종목 TTM 합산 + 최신분기 YoY. YoY 내림차순."""
    try:
        rows = con.execute(
            "SELECT sector, symbol, latest_q, capex_ttm, q_latest, q_yoy_base, fetched_at "
            f"FROM {table}"
        ).fetchall()
    except Exception:
        return None
    if not rows:
        return None
    by: dict[str, dict] = {}
    for r in rows:
        d = by.setdefault(r["sector"], {
            "ttm": 0.0, "q": 0.0, "q_base": 0.0, "n": 0,
            "latest_q": "", "top": None, "top_v": 0.0,
        })
        d["ttm"] += r["capex_ttm"] or 0
        d["n"] += 1
        if r["q_yoy_base"]:
            d["q"] += r["q_latest"]
            d["q_base"] += r["q_yoy_base"]
        d["latest_q"] = max(d["latest_q"], r["latest_q"] or "")
        if (r["capex_ttm"] or 0) > d["top_v"]:
            d["top"], d["top_v"] = r["symbol"], r["capex_ttm"]
    out = []
    for sec, d in by.items():
        out.append({
            "sector": sec, "ttm": d["ttm"], "n": d["n"],
            "yoy": round((d["q"] / d["q_base"] - 1) * 100, 1) if d["q_base"] else None,
            "latest_q": d["latest_q"], "top": d["top"],
        })
    out.sort(key=lambda x: -(x["yoy"] if x["yoy"] is not None else -1e9))
    return {"rows": out, "fetched": rows[0]["fetched_at"][:10]}


def rel_ratio_series(con, codes: list[str], bench: str, n: int = 64):
    """벤치마크 대비 가격비율 시계열 — RRG 아래 상대수익 겹침 차트용.

    반환: {code: [[date, sector/bench], ...]} (날짜 오름차순).
    리베이스(0% 기준점)는 클라이언트에서 1M/3M 윈도우로 수행.
    """
    def last_n(sym):
        rows = con.execute(
            "SELECT date, close FROM prices_daily WHERE symbol=? ORDER BY date DESC LIMIT ?",
            (sym, n),
        ).fetchall()
        return {r["date"]: r["close"] for r in rows}

    bmap = last_n(bench)
    out = {}
    for c in codes:
        if c == bench:
            continue
        pts = [
            [d, round(v / bmap[d], 6)]
            for d, v in sorted(last_n(c).items())
            if bmap.get(d)
        ]
        if len(pts) >= 2:
            out[c] = pts
    return out


def market_flows(con):
    """시장 단위 수급: (시장, 투자자)별 최근 5일/20일 누적."""
    rows = con.execute(
        "SELECT code, date, investor, net_value FROM investor_flows "
        "WHERE scope='market' ORDER BY date DESC"
    ).fetchall()
    series: dict[tuple, list[float]] = {}
    for r in rows:
        series.setdefault((r["code"], r["investor"]), []).append(r["net_value"])
    out = []
    for mkt in ("KOSPI", "KOSDAQ"):
        for inv in ("foreign", "institution", "individual"):
            vals = series.get((mkt, inv), [])
            if not vals:
                continue
            d5, d20 = sum(vals[:5]), sum(vals[:20])
            out.append({
                "mkt": mkt, "inv_ko": INV_KO[inv],
                "d5": d5, "d20": d20,
                "d5_fmt": fmt_krw(d5), "d20_fmt": fmt_krw(d20),
            })
    return out


def top_flow_stocks(con, investor: str, n: int = 10):
    # 스냅샷이 매일 쌓이므로 최신 수집분만
    rows = con.execute(
        """
        SELECT f.code, f.net_value, m.name
        FROM investor_flows f
        LEFT JOIN sector_map m ON m.stock_code = f.code
        WHERE f.scope='stock' AND f.investor=?
          AND f.date = (SELECT MAX(date) FROM investor_flows WHERE scope='stock')
        ORDER BY f.net_value DESC LIMIT ?
        """,
        (investor, n),
    ).fetchall()
    return [
        {"name": r["name"] or r["code"], "code": r["code"], "amt": fmt_krw(r["net_value"])}
        for r in rows
    ]


def sector_flows(con, names: dict):
    """KR 업종 수급 (외국인/기관 × 1주/1개월/3개월). 합산 1주 기준 정렬된 리스트."""
    by_sec: dict[str, dict[str, float]] = {}
    for scope, tag in (("sector_1w", "1w"), ("sector_1m", "1m"), ("sector_3m", "3m")):
        rows = con.execute(
            "SELECT code, investor, net_value FROM investor_flows f "
            "WHERE scope=? AND date=(SELECT MAX(date) FROM investor_flows WHERE scope=?)",
            (scope, scope),
        ).fetchall()
        for r in rows:
            d = by_sec.setdefault(r["code"], {})
            d[f"{r['investor'][0]}_{tag}"] = r["net_value"]   # f_1w, i_1w, ...
    out = []
    for sec, d in by_sec.items():
        tot_1w = d.get("f_1w", 0) + d.get("i_1w", 0)
        tot_1m = d.get("f_1m", 0) + d.get("i_1m", 0)
        tot_3m = d.get("f_3m", 0) + d.get("i_3m", 0)
        out.append({
            "name": names.get(sec, sec),
            "f_1w": fmt_krw(d.get("f_1w", 0)), "i_1w": fmt_krw(d.get("i_1w", 0)),
            "f_1w_v": d.get("f_1w", 0), "i_1w_v": d.get("i_1w", 0),
            "tot_1w": tot_1w, "tot_1w_fmt": fmt_krw(tot_1w),
            "tot_1m": tot_1m, "tot_1m_fmt": fmt_krw(tot_1m),
            "tot_3m": tot_3m, "tot_3m_fmt": fmt_krw(tot_3m),
        })
    out.sort(key=lambda x: x["tot_1w"], reverse=True)
    return out


def kr_leaders(con, sector: str = "", market: str = "", n: int = 50):
    """KR 주도주 (시총 하한 필터). sector=업종명(코스피/코스닥 통합), market=kp|kq."""
    date_row = con.execute(
        "SELECT MAX(date) d FROM analytics_daily WHERE scope='kr_stock'"
    ).fetchone()
    if date_row["d"] is None:
        return []
    min_mcap = config.load()["kr"]["leader_min_mcap"]
    where = "a.scope='kr_stock' AND a.date=?"
    params: list = [min_mcap, date_row["d"]]
    if sector:
        where += " AND m.sector_name=?"
        params.append(sector)
    if market == "kp":
        where += " AND m.sector_code LIKE '1%'"
    elif market == "kq":
        where += " AND m.sector_code LIKE '2%'"
    params.append(n)
    rows = con.execute(
        f"""
        SELECT a.code, m.name, m.sector_code, m.sector_name, sm.mcap,
               MAX(CASE WHEN a.metric='leader_score' THEN a.value END) score,
               MAX(CASE WHEN a.metric='ret_21' THEN a.value END)      ret21,
               MAX(CASE WHEN a.metric='rs_mkt_21' THEN a.value END)   rs_mkt,
               MAX(CASE WHEN a.metric='rs_sec_21' THEN a.value END)   rs_sec,
               MAX(CASE WHEN a.metric='vol_surge' THEN a.value END)   vol_surge,
               MAX(CASE WHEN a.metric='high_prox' THEN a.value END)   high_prox
        FROM analytics_daily a
        JOIN sector_map m ON m.stock_code = a.code AND m.market = 'KR'
        JOIN stock_meta sm ON sm.symbol = a.code AND sm.mcap >= ?
        WHERE {where}
        GROUP BY a.code ORDER BY score DESC LIMIT ?
        """,
        params,
    ).fetchall()
    return [dict(r) | {"mcap_fmt": fmt_krw(r["mcap"]) if r["mcap"] else None} for r in rows]


def kr_sector_strength(con) -> list[dict]:
    """업종명(코스피/코스닥 통합) 단위 평균 주도점수 — 필터 pill 정렬·아웃퍼폼 표시용.

    시총 하한 통과 종목만 집계 (테이블과 같은 유니버스).
    """
    date_row = con.execute(
        "SELECT MAX(date) d FROM analytics_daily WHERE scope='kr_stock'"
    ).fetchone()
    if date_row["d"] is None:
        return []
    min_mcap = config.load()["kr"]["leader_min_mcap"]
    rows = con.execute(
        """
        SELECT m.sector_name name, COUNT(*) n, AVG(a.value) avg_score
        FROM analytics_daily a
        JOIN sector_map m ON m.stock_code = a.code AND m.market = 'KR'
        JOIN stock_meta sm ON sm.symbol = a.code AND sm.mcap >= ?
        WHERE a.scope='kr_stock' AND a.metric='leader_score' AND a.date=?
        GROUP BY m.sector_name
        HAVING n >= 2
        ORDER BY avg_score DESC
        """,
        (min_mcap, date_row["d"]),
    ).fetchall()
    return [
        {"name": r["name"], "n": r["n"], "score": round(r["avg_score"], 0)}
        for r in rows
    ]


def bench_snapshot(con, sym: str):
    """벤치마크 카드: 최근 종가 + 21거래일 수익률(%)."""
    rows = con.execute(
        "SELECT date, close FROM prices_daily WHERE symbol=? ORDER BY date DESC LIMIT 22",
        (sym,),
    ).fetchall()
    if not rows:
        return None
    last = rows[0]
    ret21 = (last["close"] / rows[-1]["close"] - 1) * 100 if len(rows) >= 22 else None
    return {"date": last["date"], "close": last["close"], "ret21": ret21}


def _macro_series(con, sym: str, n: int = 270) -> list[float]:
    rows = con.execute(
        "SELECT close FROM prices_daily WHERE symbol=? ORDER BY date DESC LIMIT ?", (sym, n)
    ).fetchall()
    return [r["close"] for r in reversed(rows)]


def _macro_stats(vals: list[float], scale: float = 1.0, pct_change: bool = True):
    """현재값·일변화·30일평균 대비·52주 레인지 위치·1년 백분위."""
    s = [v * scale for v in vals if v is not None]
    if len(s) < 40:
        return None
    cur, prev = s[-1], s[-2]
    chg = (cur / prev - 1) * 100 if pct_change else cur - prev
    ma30 = sum(s[-30:]) / 30
    ma = (cur / ma30 - 1) * 100 if pct_change else cur - ma30
    yr = s[-252:]
    lo, hi = min(yr), max(yr)
    pos = 50.0 if hi == lo else (cur - lo) / (hi - lo) * 100
    top = round(100 * sum(1 for x in yr if x >= cur) / len(yr))
    return {"cur": cur, "chg": chg, "ma": ma, "lo": lo, "hi": hi, "pos": pos, "top": top}


def macro_context(con) -> list[dict]:
    """시장 컨텍스트 카드 데이터 (VIX/WTI/금/10Y/스프레드/HYG)."""
    # 야후 ^TNX/^IRX는 이미 %단위 (4.55 = 4.55%)
    spread = [
        r["v"] for r in con.execute(
            """
            SELECT (a.close - b.close) v FROM prices_daily a
            JOIN prices_daily b ON b.date = a.date AND b.symbol = '^IRX'
            WHERE a.symbol = '^TNX' ORDER BY a.date DESC LIMIT 270
            """
        ).fetchall()
    ][::-1]

    spec = [
        ("⚠", "VIX 공포지수", _macro_series(con, "^VIX"), 1.0, True, lambda v: f"{v:.1f}"),
        ("🛢", "WTI 원유", _macro_series(con, "CL=F"), 1.0, True, lambda v: f"${v:.2f}"),
        ("◆", "금", _macro_series(con, "GC=F"), 1.0, True, lambda v: f"${v:,.0f}"),
        ("↗", "US 10Y 국채", _macro_series(con, "^TNX"), 1.0, False, lambda v: f"{v:.2f}%"),
        ("⚖", "10Y-3M 스프레드", spread, 1.0, False, lambda v: f"{v:.2f}%"),
        ("▤", "HYG 하이일드", _macro_series(con, "HYG"), 1.0, True, lambda v: f"${v:.2f}"),
    ]
    out = []
    for icon, label, vals, scale, pctchg, fmt in spec:
        st = _macro_stats(vals, scale, pctchg)
        if not st:
            continue
        unit = "%" if pctchg else "%p"
        out.append({
            "icon": icon, "label": label,
            "val": fmt(st["cur"]),
            "chg": f"{st['chg']:+.2f}{unit}", "chg_up": st["chg"] >= 0,
            "ma": f"{st['ma']:+.1f}{unit}", "ma_up": st["ma"] >= 0,
            "top": st["top"], "pos": round(st["pos"], 1),
            "lo": fmt(st["lo"]), "hi": fmt(st["hi"]),
        })
    return out


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


def investor_trend(con, mkt: str = "KOSPI", days: int = 60):
    """투자자별 누적 순매수 시계열 (LWC 라인 3개 + 합계)."""
    rows = con.execute(
        "SELECT date, investor, net_value FROM investor_flows "
        "WHERE scope='market' AND code=? ORDER BY date",
        (mkt,),
    ).fetchall()
    by_date: dict[str, dict[str, float]] = {}
    for r in rows:
        by_date.setdefault(r["date"], {})[r["investor"]] = r["net_value"]
    dates = sorted(by_date)[-days:]
    if len(dates) < 10:
        return None
    series = {inv: [] for inv in ("foreign", "institution", "individual")}
    cum = {inv: 0.0 for inv in series}
    for d in dates:
        for inv in series:
            cum[inv] += by_date[d].get(inv, 0.0)
            series[inv].append({"time": d, "value": round(cum[inv] / 1e12, 3)})  # 조원
    totals = {
        inv: {"v": cum[inv], "fmt": fmt_krw(cum[inv]), "ko": INV_KO[inv]}
        for inv in series
    }
    return {"series": series, "totals": totals, "n_days": len(dates)}


def treasury_line(con):
    """미국 국채 5/10/30년 + 10Y-5Y 스프레드 한 줄."""
    out = {}
    for sym, key in (("^FVX", "y5"), ("^TNX", "y10"), ("^TYX", "y30")):
        s = _macro_series(con, sym, 5)
        if len(s) < 2:
            return None
        out[key] = {"v": s[-1], "chg": s[-1] - s[-2]}
    spread = out["y10"]["v"] - out["y5"]["v"]
    out["spread"] = {"v": spread, "normal": spread >= 0}
    return out


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


EARN_TIME_KO = {
    "time-pre-market": "장전",
    "time-after-hours": "장마감 후",
    "time-not-supplied": "미정",
}


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


def theme_radar(con, surge_pct: float = 30.0, top: int = 5) -> list[dict]:
    """테마 레이더 (관찰용) — 업종별 1개월 +30% 급등 종목 수 + 거래대금 쏠림.

    매매 신호가 아님: 추격 백테스트(scripts/theme_chase_backtest.py) 결과
    급등주 추격은 중앙값 -14%의 복권 구조. '지금 돈이 노는 곳' 관찰용.
    """
    date_row = con.execute(
        "SELECT MAX(date) d FROM analytics_daily WHERE scope='kr_stock' AND metric='ret_21'"
    ).fetchone()
    if not date_row["d"]:
        return []
    rows = con.execute(
        """
        SELECT m.sector_code, m.sector_name, m.name, a.value ret21
        FROM analytics_daily a
        JOIN sector_map m ON m.stock_code = a.code AND m.market = 'KR'
        WHERE a.scope='kr_stock' AND a.metric='ret_21' AND a.date=? AND a.value >= ?
        ORDER BY a.value DESC
        """,
        (date_row["d"], surge_pct),
    ).fetchall()
    vshare = {
        r["code"]: r["value"] for r in con.execute(
            "SELECT code, value FROM analytics_daily WHERE scope='kr_sector' "
            "AND metric='val_share_ratio' AND date="
            "(SELECT MAX(date) FROM analytics_daily WHERE scope='kr_sector' AND metric='val_share_ratio')"
        )
    }
    by_sec: dict[str, dict] = {}
    for r in rows:
        d = by_sec.setdefault(r["sector_code"], {
            "name": r["sector_name"], "kq": r["sector_code"].startswith("2"),
            "count": 0, "tops": [],
        })
        d["count"] += 1
        if len(d["tops"]) < 3:
            d["tops"].append(f"{r['name']} +{r['ret21']:.0f}%")
    ranked = sorted(by_sec.items(), key=lambda x: -x[1]["count"])[:top]
    out = []
    for code, d in ranked:
        d["vshare"] = vshare.get(code)
        out.append(d)
    return out


def market_ratio(con, num: str = "2001", den: str = "1001", ma: int = 50):
    """코스닥/코스피 상대비율 — 어느 시장이 아웃퍼폼 중인가 (비율의 50일선 기준)."""
    rows = con.execute(
        """
        SELECT a.date, a.close / b.close v
        FROM prices_daily a JOIN prices_daily b ON b.date = a.date AND b.symbol = ?
        WHERE a.symbol = ? ORDER BY a.date DESC LIMIT 300
        """,
        (den, num),
    ).fetchall()
    vals = [r["v"] for r in reversed(rows)]
    if len(vals) < ma + 63:
        return None
    cur = vals[-1]
    return {
        "ratio": cur,
        "chg21": (cur / vals[-22] - 1) * 100,
        "chg63": (cur / vals[-64] - 1) * 100,
        "above": cur >= sum(vals[-ma:]) / ma,
    }


def classify_vix_signal(vix: float, vvix: float, cooling: bool, fng: float | None = None) -> dict:
    """매수 신호등 — 근거: scripts/vvix_backtest.py + fng_backtest.py.

    보류: [VIX<20 & VVIX≥95](전조) / [VIX 20~30 & VVIX<95](함정)
    매수: [VIX 20~30 & VVIX≥95](승률 84%) / VIX 30+ / VIX 35+ & VVIX 냉각(적극)
    회피: 평온장(VIX<20 & VVIX<95)인데 F&G≥75 (극단탐욕: 승률 79→57%)
    """
    if vix >= 35 and cooling:
        return {"state": "buy3", "emoji": "🟢🟢", "cls": "pos",
                "label": "적극 매수 — 공포 정점 통과",
                "desc": "VIX 35+ & VVIX 냉각 · 3개월 중앙값 +9.8%"}
    if vix >= 30:
        return {"state": "buy2", "emoji": "🟢", "cls": "pos",
                "label": "분할 매수 구간",
                "desc": "VIX 30+ · 역사적 승률 72~83%"}
    if vix >= 20 and vvix >= 95:
        return {"state": "buy1", "emoji": "🟢", "cls": "pos",
                "label": "1차 매수 구간 — 급성 공포",
                "desc": "VIX 20~30 & VVIX 95+ · 승률 84% · 중앙값 +6.9%"}
    if vix >= 20:
        return {"state": "hold_trap", "emoji": "🔴", "cls": "neg",
                "label": "매수 보류 — 함정 구간",
                "desc": "공포 없는 하락 초입 (VIX 20~30 & VVIX<95) · 승률 65%"}
    if vvix >= 95:
        return {"state": "hold_pre", "emoji": "🔴", "cls": "neg",
                "label": "매수 보류 — 전조 경보",
                "desc": "평온 속 크래시 헤지 수요 급증 · 승률 66%"}
    if fng is not None and fng >= 75:
        return {"state": "avoid_greed", "emoji": "🟠", "cls": "hot",
                "label": "과열 주의 — 신규매수 자제",
                "desc": "평온장 극단탐욕 (F&G 75+) · 승률 79→57%, 중앙값 +4.7→+1.6%"}
    return {"state": "neutral", "emoji": "⚪", "cls": "",
            "label": "평시 — 신호 없음",
            "desc": "레짐·주도주 신호를 따르세요"}


def vix_signal(con):
    vix = _macro_series(con, "^VIX", 5)
    vvix = _macro_series(con, "^VVIX", 5)
    if not vix or len(vvix) < 5:
        return None
    v, w = vix[-1], vvix[-1]
    w5 = sum(vvix[-5:]) / 5
    fng_row = con.execute(
        "SELECT value FROM sentiment_daily WHERE metric='fear_greed' ORDER BY date DESC LIMIT 1"
    ).fetchone()
    fng = fng_row["value"] if fng_row else None
    sig = classify_vix_signal(v, w, cooling=w < w5, fng=fng)
    sig.update({"vix": v, "vvix": w, "vvix5": w5, "fng": fng})
    return sig


def regime(con, sym: str, ma_days: int = 200):
    """시장 레짐: 종가 vs 200일 이평 — 모멘텀 크래시 회피용 신호등."""
    rows = con.execute(
        "SELECT close FROM prices_daily WHERE symbol=? ORDER BY date DESC LIMIT ?",
        (sym, ma_days),
    ).fetchall()
    if len(rows) < ma_days:
        return None
    closes = [r["close"] for r in rows]
    ma = sum(closes) / len(closes)
    return {"above": closes[0] >= ma, "dev": (closes[0] / ma - 1) * 100}


def sentiment_latest(con):
    """지표별 최신값: {metric: {date, value}}."""
    rows = con.execute(
        "SELECT metric, date, value FROM sentiment_daily sd "
        "WHERE date = (SELECT MAX(date) FROM sentiment_daily WHERE metric = sd.metric)"
    ).fetchall()
    return {r["metric"]: {"date": r["date"], "value": r["value"]} for r in rows}


def overheat_list(con, scope: str, names: dict):
    """현재 과열 플래그가 켜진 코드 목록."""
    date = store.latest_date(con, scope, "overheat")
    if date is None:
        return []
    rows = con.execute(
        "SELECT code FROM analytics_daily WHERE scope=? AND date=? AND metric='overheat' AND value=1",
        (scope, date),
    ).fetchall()
    return [names.get(r["code"], r["code"]) for r in rows]


def freshness(con):
    """수집기별 마지막 실행 상태."""
    rows = con.execute(
        """
        SELECT collector, run_at, status, rows FROM collector_runs cr
        WHERE id = (SELECT MAX(id) FROM collector_runs WHERE collector = cr.collector)
        ORDER BY collector
        """
    ).fetchall()
    return [dict(r) for r in rows]


def kr_index_names(con) -> dict:
    return {
        r["stock_code"]: r["name"]
        for r in con.execute("SELECT stock_code, name FROM sector_map WHERE market='KR_INDEX'")
    }
