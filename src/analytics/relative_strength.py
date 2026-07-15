"""상대강도(RS) 랭킹 — 기간별 수익률과 벤치마크 대비 초과수익.

analytics_daily에 최신일 기준으로 저장:
  ret_{w}: w거래일 수익률(%),  rs_{w}: 벤치마크 대비 초과수익(%p)
"""
import pandas as pd

from src import config
from src.analytics import store
from src.analytics.data import load_closes


def compute(con, scope: str, symbols: list[str], benchmark: str) -> int:
    cfg = config.load()["analytics"]
    universe = list(dict.fromkeys(symbols + [benchmark]))
    px = load_closes(con, universe).dropna(how="all")
    date = px.index[-1]

    metrics = [f"{p}_{w}" for w in cfg["rs_windows"] for p in ("ret", "rs")]
    rows = []
    for w in cfg["rs_windows"]:
        ret = px.pct_change(w, fill_method=None).iloc[-1]
        rs = ret - ret[benchmark]
        for sym in symbols:
            if pd.isna(ret.get(sym)):
                continue
            rows.append((date, scope, sym, f"ret_{w}", round(float(ret[sym]) * 100, 2)))
            rows.append((date, scope, sym, f"rs_{w}", round(float(rs[sym]) * 100, 2)))
    return store.replace_metrics(con, scope, metrics, rows)
