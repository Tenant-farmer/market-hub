"""RRG-lite 로테이션 사분면.

rs_ratio = 100 * (심볼/벤치마크 상대가격) / 그 63일 평균
rs_mom   = 100 * rs_ratio / 21일 전 rs_ratio
사분면: 1=주도(Leading) 2=약화(Weakening) 3=침체(Lagging) 4=개선(Improving)

대시보드가 궤적(trail)을 그릴 수 있게 최근 keep_days일치를 전부 저장.
"""
import pandas as pd

from src import config
from src.analytics import store
from src.analytics.data import load_closes

METRICS = ["rs_ratio", "rs_mom", "quadrant", "lead_streak"]


def _quadrant(ratio: float, mom: float) -> int:
    if ratio >= 100:
        return 1 if mom >= 100 else 2
    return 4 if mom >= 100 else 3


def compute(con, scope: str, symbols: list[str], benchmark: str, keep_days: int = 120) -> int:
    cfg = config.load()["analytics"]
    w, m = cfg["rrg_window"], cfg["rrg_momentum"]
    px = load_closes(con, list(dict.fromkeys(symbols + [benchmark])))

    rs_line = px[symbols].div(px[benchmark], axis=0)
    ratio_full = 100 * rs_line / rs_line.rolling(w).mean()
    mom_full = 100 * ratio_full / ratio_full.shift(m)
    # Leading 체류일: 이벤트 스터디 결과 체류 21일 미만 '반짝' 진입은 이후 성과가
    # 오히려 마이너스(승률 39%), 21일 이상 유지가 지속 신호(승률 60%) — 판별용으로 저장
    lead_full = (ratio_full >= 100) & (mom_full >= 100) & ratio_full.notna() & mom_full.notna()
    rs_ratio = ratio_full.tail(keep_days)
    rs_mom = mom_full.tail(keep_days)

    rows = []
    last_date = px.index[-1]
    for sym in symbols:
        pair = pd.concat([rs_ratio[sym], rs_mom[sym]], axis=1, keys=["ratio", "mom"]).dropna()
        for date, r in pair.iterrows():
            rows.append((date, scope, sym, "rs_ratio", round(float(r["ratio"]), 3)))
            rows.append((date, scope, sym, "rs_mom", round(float(r["mom"]), 3)))
            rows.append((date, scope, sym, "quadrant", _quadrant(r["ratio"], r["mom"])))
        rows.append((last_date, scope, sym, "lead_streak", current_streak(lead_full[sym].values)))
    return store.replace_metrics(con, scope, METRICS, rows)


def current_streak(lead_values) -> int:
    """마지막 시점 기준 연속 Leading 일수."""
    streak = 0
    while streak < len(lead_values) and lead_values[-(streak + 1)]:
        streak += 1
    return streak
