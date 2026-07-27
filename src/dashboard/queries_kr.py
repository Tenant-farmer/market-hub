"""KR 수급·주도주 조회 — queries.py에서 도메인 분리 (2026-07-27 리팩토링).

투자자별 매매동향(외인·기관·개인), KR 주도주 랭킹, 업종 강도, 지수명 매핑.
queries.py가 re-export하므로 호출부는 queries.X 그대로 사용한다.
"""
from src import config
from src.dashboard.fmt import INV_KO, fmt_krw


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
    order = {"score": "score DESC", "rs21": "rs_mkt DESC", "score63": "rs_mkt63 DESC",
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


def kr_index_names(con) -> dict:
    return {
        r["stock_code"]: r["name"]
        for r in con.execute("SELECT stock_code, name FROM sector_map WHERE market='KR_INDEX'")
    }
