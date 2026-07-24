"""대시보드 페이지 공용 조회 (섹터·주도주·수급·종목허브·CapEx).

매크로/신호/레짐은 queries_macro, 일정은 queries_calendar로 분리 — 아래에서 re-export하므로
호출부는 queries.macro_context(...) / queries.fed_watch(...) 그대로 사용한다.
"""
from src import config
from src.analytics import store
from src.dashboard.fmt import INV_KO, QUAD_DESC, fmt_krw

from src.dashboard.queries_calendar import (  # noqa: F401  (re-export)
    earnings_upcoming, econ_upcoming, fed_watch,
)
from src.dashboard.queries_macro import (  # noqa: F401  (re-export)
    bench_snapshot, classify_vix_signal, kr_signal, macro_context, market_ratio,
    regime, sentiment_latest, treasury_line, vix_signal,
)

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


def ohlcv(con, sym: str, n: int = 2600):
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

    ascope = "kr_stock" if mkt == "kr" else "us_stock"
    body = f"""
        FROM sector_map m
        JOIN stock_meta s ON s.symbol = m.stock_code
        JOIN prices_daily p ON p.symbol = m.stock_code AND p.date = ?
        LEFT JOIN prices_daily pp ON pp.symbol = m.stock_code AND pp.date = ?
        LEFT JOIN analytics_daily a ON a.code = m.stock_code AND a.scope = ?
            AND a.metric = 'leader_score'
            AND a.date = (SELECT MAX(date) FROM analytics_daily WHERE scope = ? AND metric = 'leader_score')
        LEFT JOIN analytics_daily a63 ON a63.code = m.stock_code AND a63.scope = ?
            AND a63.metric = 'rs_mkt_63'
            AND a63.date = (SELECT MAX(date) FROM analytics_daily WHERE scope = ? AND metric = 'rs_mkt_63')
        WHERE {" AND ".join(where)}
    """
    qargs = [d0, d1, ascope, ascope, ascope, ascope, *args]
    total = con.execute(f"SELECT COUNT(*) n {body}", qargs).fetchone()["n"]
    order = {"vol": "p.volume DESC", "score": "a.value DESC",
             "score63": "a63.value DESC"}.get(sort, "s.mcap DESC")
    rows = con.execute(
        f"SELECT m.stock_code code, m.name, m.sector_name sector, s.mcap, "
        f"p.close, p.volume, pp.close prev, a.value score, a63.value score63 {body} "
        f"ORDER BY {order} LIMIT ? OFFSET ?",
        [*qargs, per, (page - 1) * per],
    ).fetchall()
    out = []
    for r in rows:
        out.append({
            "code": r["code"], "name": r["name"] or r["code"], "sector": r["sector"] or "",
            "mcap": r["mcap"], "close": r["close"], "volume": r["volume"], "score": r["score"],
            "score63": r["score63"],
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


def kr_leaders(con, sector: str = "", market: str = "", n: int = 50, sort: str = "score"):
    """KR 주도주 (시총 하한 필터). sector=업종명(코스피/코스닥 통합), market=kp|kq."""
    date_row = con.execute(
        "SELECT MAX(date) d FROM analytics_daily WHERE scope='kr_stock'"
    ).fetchone()
    if date_row["d"] is None:
        return []
    order = {"score": "score DESC", "score63": "rs_mkt63 DESC",
             "mcap": "sm.mcap DESC", "vol": "vol_surge DESC"}.get(sort, "score DESC")
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
               MAX(CASE WHEN a.metric='rs_mkt_63' THEN a.value END)   rs_mkt63,
               MAX(CASE WHEN a.metric='rs_sec_21' THEN a.value END)   rs_sec,
               MAX(CASE WHEN a.metric='vol_surge' THEN a.value END)   vol_surge,
               MAX(CASE WHEN a.metric='high_prox' THEN a.value END)   high_prox
        FROM analytics_daily a
        JOIN sector_map m ON m.stock_code = a.code AND m.market = 'KR'
        JOIN stock_meta sm ON sm.symbol = a.code AND sm.mcap >= ?
        WHERE {where}
        GROUP BY a.code ORDER BY {order} LIMIT ?
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
