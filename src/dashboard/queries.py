"""대시보드 페이지 공용 조회."""
from src import config
from src.analytics import store
from src.dashboard.fmt import INV_KO, QUAD_DESC, fmt_krw

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
    return [{"name": r["name"] or r["code"], "amt": fmt_krw(r["net_value"])} for r in rows]


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


def kr_leaders(con, sector: str = "", n: int = 50):
    """KR 주도주 (시총 하한 필터)."""
    date_row = con.execute(
        "SELECT MAX(date) d FROM analytics_daily WHERE scope='kr_stock'"
    ).fetchone()
    if date_row["d"] is None:
        return []
    min_mcap = config.load()["kr"]["leader_min_mcap"]
    where = "a.scope='kr_stock' AND a.date=?"
    params: list = [min_mcap, date_row["d"]]
    if sector:
        where += " AND m.sector_code=?"
        params.append(sector)
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
