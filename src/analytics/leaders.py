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
                   bench: str = "SPY") -> int:
    """종목 주도점수 v2 — 6성분 백분위 가중합.

    3개월 시장RS 25% + 1개월 시장RS 20% + 1개월 절대수익 20%
    + 52주 고점比 20% + 1개월 업종RS 10% + 거래량 급증 5%
    (가중치는 settings [analytics.leader_weights])
    """
    w = config.load()["analytics"]["leader_weights"]

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

    ret21 = px.pct_change(21, fill_method=None).iloc[-1]
    ret63 = px.pct_change(63, fill_method=None).iloc[-1]
    v5 = vol.tail(5).mean()
    v20 = vol.tail(20).mean()
    hi52 = px[stocks].rolling(252, min_periods=120).max().iloc[-1]
    last = px[stocks].iloc[-1]

    comp: dict[str, dict[str, float]] = {}
    for s in stocks:
        sec = smap[s]
        if (pd.isna(ret21.get(s)) or pd.isna(ret63.get(s))
                or sec not in ret21.index or pd.isna(ret21[sec])
                or pd.isna(v5.get(s)) or not v20.get(s)
                or pd.isna(hi52.get(s)) or hi52[s] <= 0):
            continue
        comp[s] = {
            "rs_mkt_21": ret21[s] - ret21[bench],
            "rs_mkt_63": ret63[s] - ret63[bench],
            "abs_21": ret21[s],
            "high_prox": float(last[s] / hi52[s]),
            "rs_sec_21": ret21[s] - ret21[sec],
            "vol_surge": v5[s] / v20[s],
        }
    if not comp:
        return 0

    ranks = {
        k: _pct_rank({s: c[k] for s, c in comp.items()})
        for k in ("rs_mkt_21", "rs_mkt_63", "abs_21", "high_prox", "rs_sec_21", "vol_surge")
    }

    date = px.index[-1]
    out = []
    for s, c in comp.items():
        score = 100 * sum(w[k] * ranks[k][s] for k in ranks)
        out.append((date, scope, s, "leader_score", round(score, 1)))
        out.append((date, scope, s, "ret_21", round(c["abs_21"] * 100, 2)))
        out.append((date, scope, s, "rs_mkt_21", round(c["rs_mkt_21"] * 100, 2)))
        out.append((date, scope, s, "rs_mkt_63", round(c["rs_mkt_63"] * 100, 2)))
        out.append((date, scope, s, "rs_sec_21", round(c["rs_sec_21"] * 100, 2)))
        out.append((date, scope, s, "vol_surge", round(c["vol_surge"], 2)))
        out.append((date, scope, s, "high_prox", round(c["high_prox"], 4)))
    metrics = ["leader_score", "ret_21", "rs_mkt_21", "rs_mkt_63",
               "rs_sec_21", "vol_surge", "high_prox"]
    return store.replace_metrics(con, scope, metrics, out)
