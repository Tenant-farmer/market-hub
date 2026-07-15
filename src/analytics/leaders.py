"""주도 점수.

섹터 단위: leader_score = 100 × (w1·pct_rank(rs_63) + w2·pct_rank(rs_21) + w3·pct_rank(rs_mom))
종목 단위: leader_score = 100 × (w1·pct_rank(시장대비 RS) + w2·pct_rank(섹터대비 RS) + w3·pct_rank(거래량급증))
— 각 성분은 같은 scope 내 백분위(0~1). "무엇이 제일 주도인가"를 한 숫자로.
"""
import pandas as pd

from src import config
from src.analytics import store
from src.analytics.data import load_closes, load_field


def _pct_rank(values: dict[str, float]) -> dict[str, float]:
    codes = sorted(values, key=lambda c: values[c])
    n = len(codes)
    if n <= 1:
        return {c: 1.0 for c in codes}
    return {c: i / (n - 1) for i, c in enumerate(codes)}


def compute_sector(con, scope: str) -> int:
    w = config.load()["analytics"]["sector_leader_weights"]
    date, rows = store.pivot_latest(
        con, scope, {"rs63": "rs_63", "rs21": "rs_21", "mom": "rs_mom"},
        date=store.latest_date(con, scope, "rs_21"),
    )
    comp = {
        r["code"]: (r["rs63"], r["rs21"], r["mom"])
        for r in rows
        if r["rs63"] is not None and r["rs21"] is not None and r["mom"] is not None
    }
    if not comp:
        return 0

    p63 = _pct_rank({c: v[0] for c, v in comp.items()})
    p21 = _pct_rank({c: v[1] for c, v in comp.items()})
    pmo = _pct_rank({c: v[2] for c, v in comp.items()})

    out = [
        (date, scope, c,
         "leader_score",
         round(100 * (w["rs_63"] * p63[c] + w["rs_21"] * p21[c] + w["rs_mom"] * pmo[c]), 1))
        for c in comp
    ]
    return store.replace_metrics(con, scope, ["leader_score"], out)


def compute_stocks(con, scope: str = "us_stock", market: str = "US_STOCK",
                   bench: str = "SPY", window: int = 21) -> int:
    """종목 주도점수: 시장대비 RS + 소속섹터대비 RS + 거래량 급증 (백분위 가중합).

    부가 지표로 52주 고점 대비 위치(high_prox)도 저장 (점수 미반영, 표시용).
    """
    w_cfg = config.load()["analytics"]["leader_weights"]
    # flow_streak은 KR 수급 전용 — 없는 시장은 나머지 가중치를 재정규화
    w = {k: w_cfg[k] for k in ("rs_market", "rs_sector", "volume_surge")}
    tot = sum(w.values())
    w = {k: v / tot for k, v in w.items()}

    smap = {
        r["stock_code"]: r["sector_code"]
        for r in con.execute("SELECT stock_code, sector_code FROM sector_map WHERE market=?", (market,))
    }
    if not smap:
        return 0
    stocks = sorted(smap)
    etfs = sorted(set(smap.values()))

    px = load_closes(con, stocks + etfs + [bench])
    vol = load_field(con, stocks, "volume").reindex(px.index)
    stocks = [s for s in stocks if s in px.columns]

    ret = px.pct_change(window, fill_method=None).iloc[-1]
    rs_mkt = {s: ret[s] - ret[bench] for s in stocks if pd.notna(ret.get(s))}
    rs_sec = {
        s: ret[s] - ret[smap[s]]
        for s in rs_mkt
        if smap[s] in ret.index and pd.notna(ret[smap[s]])
    }
    v5 = vol.tail(5).mean()
    v20 = vol.tail(20).mean()
    vsurge = {s: v5[s] / v20[s] for s in rs_sec if pd.notna(v5.get(s)) and v20.get(s)}
    hi52 = px[stocks].rolling(252, min_periods=120).max().iloc[-1]
    last = px[stocks].iloc[-1]

    universe = sorted(set(rs_mkt) & set(rs_sec) & set(vsurge))
    if not universe:
        return 0
    p_mkt = _pct_rank({s: rs_mkt[s] for s in universe})
    p_sec = _pct_rank({s: rs_sec[s] for s in universe})
    p_vol = _pct_rank({s: vsurge[s] for s in universe})

    date = px.index[-1]
    out = []
    for s in universe:
        score = 100 * (w["rs_market"] * p_mkt[s] + w["rs_sector"] * p_sec[s] + w["volume_surge"] * p_vol[s])
        out.append((date, scope, s, "leader_score", round(score, 1)))
        out.append((date, scope, s, "rs_mkt_21", round(rs_mkt[s] * 100, 2)))
        out.append((date, scope, s, "rs_sec_21", round(rs_sec[s] * 100, 2)))
        out.append((date, scope, s, "vol_surge", round(vsurge[s], 2)))
        if pd.notna(hi52.get(s)) and hi52[s] > 0:
            out.append((date, scope, s, "high_prox", round(float(last[s] / hi52[s]), 4)))
    metrics = ["leader_score", "rs_mkt_21", "rs_sec_21", "vol_surge", "high_prox"]
    return store.replace_metrics(con, scope, metrics, out)
