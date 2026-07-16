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
