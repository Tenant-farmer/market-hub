"""매크로·매수신호·레짐 조회 — 개요 페이지의 시장 컨텍스트/신호등 영역.

queries.py에서 도메인 분리 (함수 이동, 로직 불변). queries가 re-export하므로
호출부는 queries.macro_context(...) 그대로 사용한다.
"""


def bench_snapshot(con, sym: str):
    """벤치마크 카드: 최근 종가 + 21거래일 수익률(%) + 52주 고점比."""
    rows = con.execute(
        "SELECT date, close FROM prices_daily WHERE symbol=? ORDER BY date DESC LIMIT 252",
        (sym,),
    ).fetchall()
    if not rows:
        return None
    last = rows[0]
    ret21 = (last["close"] / rows[21]["close"] - 1) * 100 if len(rows) >= 22 else None
    hi52 = max(r["close"] for r in rows)
    return {
        "date": last["date"], "close": last["close"], "ret21": ret21,
        "off_hi": (last["close"] / hi52 - 1) * 100 if hi52 else None,
    }


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

    # 한국 국고채 장단기 (ECOS — 키 없으면 시리즈가 비어 카드 자동 생략)
    kr_spread = [
        r["v"] for r in con.execute(
            """
            SELECT (a.close - b.close) v FROM prices_daily a
            JOIN prices_daily b ON b.date = a.date AND b.symbol = 'ECOS:KTB3Y'
            WHERE a.symbol = 'ECOS:KTB10Y' ORDER BY a.date DESC LIMIT 270
            """
        ).fetchall()
    ][::-1]

    # 1줄: 변동성·위험자산 / 2줄: 금리·크레딧 (cards6 = 6열 고정이라 12개 → 정확히 2줄)
    spec = [
        ("🇰🇷", "VKOSPI 한국공포", _macro_series(con, "VKOSPI"), 1.0, True, lambda v: f"{v:.1f}"),
        ("⚠", "VIX 공포지수", _macro_series(con, "^VIX"), 1.0, True, lambda v: f"{v:.1f}"),
        ("⚡", "VVIX 헤지수요", _macro_series(con, "^VVIX"), 1.0, True, lambda v: f"{v:.0f}"),
        ("🛢", "WTI 원유", _macro_series(con, "CL=F"), 1.0, True, lambda v: f"${v:.2f}"),
        ("◆", "금", _macro_series(con, "GC=F"), 1.0, True, lambda v: f"${v:,.0f}"),
        ("₿", "BTC 비트코인", _macro_series(con, "BTC-USD"), 1.0, True, lambda v: f"${v:,.0f}"),
        ("↗", "US 10Y 국채", _macro_series(con, "^TNX"), 1.0, False, lambda v: f"{v:.2f}%"),
        ("⚖", "10Y-3M 스프레드", spread, 1.0, False, lambda v: f"{v:.2f}%"),
        ("▤", "HYG 하이일드", _macro_series(con, "HYG"), 1.0, True, lambda v: f"${v:.2f}"),
        ("🇰🇷", "KR 국고채 10Y", _macro_series(con, "ECOS:KTB10Y"), 1.0, False, lambda v: f"{v:.2f}%"),
        ("🇰🇷", "KR 국고채 3Y", _macro_series(con, "ECOS:KTB3Y"), 1.0, False, lambda v: f"{v:.2f}%"),
        ("⚖", "KR 10Y-3Y", kr_spread, 1.0, False, lambda v: f"{v:.2f}%"),
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


def kr_signal(con):
    """KR 전용 매수신호 — VKOSPI≥30 & KOSPI 52주 고점 대비 -5% 이하.

    근거: scripts/vkospi_backtest.py (2010~24: +63d 승률 75%/중앙 +5.3%, 저점지연 22일
    — 글로벌 VIX 신호의 58일 시차 해소). 낙폭 조건은 멜트업(상승 과열 변동성)을
    공포와 구분 — 고점 근처 고변동에선 발동하지 않음. VKOSPI 미수집이면 None.
    """
    try:
        vk = _macro_series(con, "VKOSPI", 5)
        ks = _macro_series(con, "1001", 260)          # KOSPI 지수 (52주 창)
    except Exception:                                 # prices_daily 미생성 (테스트/신규 설치)
        return None
    if not vk or len(ks) < 200:
        return None
    v, dd = vk[-1], (ks[-1] / max(ks) - 1) * 100
    if v >= 30 and dd <= -5:
        sig = {"state": "buy", "emoji": "🟢", "cls": "pos",
               "label": "KR 매수 구간 — 로컬 공포",
               "desc": "VKOSPI 30+ & 낙폭 5%+ · 2010~24 승률 75% · 중앙값 +5.3%"}
    elif v >= 30:
        sig = {"state": "hold_melt", "emoji": "🟠", "cls": "hot",
               "label": "KR 보류 — 과열 변동성",
               "desc": "VKOSPI 30+ 이나 낙폭 5% 미달 — 공포 아닌 멜트업 변동성"}
    else:
        sig = {"state": "neutral", "emoji": "⚪", "cls": "",
               "label": "KR 평시 — 신호 없음",
               "desc": "VKOSPI 30 미만"}
    sig.update({"vkospi": v, "kospi_dd": dd})
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
