"""과열 플래그 — RSI(Wilder) + 장기이평 이격도.

최신일 기준 저장: rsi, ma_dev(200MA 대비 비율-1), overheat(0/1)
"""
import pandas as pd

from src import config
from src.analytics import store
from src.analytics.data import load_closes

METRICS = ["rsi", "ma_dev", "overheat"]


def _rsi(px: pd.DataFrame, period: int) -> pd.DataFrame:
    delta = px.diff()
    up = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    dn = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    return 100 - 100 / (1 + up / dn)


def compute(con, scope: str, symbols: list[str]) -> int:
    cfg = config.load()["analytics"]
    px = load_closes(con, symbols)
    rsi = _rsi(px, cfg["rsi_period"]).iloc[-1]
    ma_dev = (px / px.rolling(cfg["ma_long"]).mean() - 1).iloc[-1]
    date = px.index[-1]

    rows = []
    for sym in symbols:
        if pd.isna(rsi.get(sym)) or pd.isna(ma_dev.get(sym)):
            continue
        hot = int(rsi[sym] >= cfg["overheat_rsi"] or ma_dev[sym] >= cfg["overheat_ma_dev"])
        rows.append((date, scope, sym, "rsi", round(float(rsi[sym]), 1)))
        rows.append((date, scope, sym, "ma_dev", round(float(ma_dev[sym]), 4)))
        rows.append((date, scope, sym, "overheat", hot))
    return store.replace_metrics(con, scope, METRICS, rows)
