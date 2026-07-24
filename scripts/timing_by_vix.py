"""매수 시점 검증 — VIX/VVIX 국면별로 어느 전략이 유리한가.

질문: 언제 사는 게 통계적으로 유리한가? 평온장 vs 공포장(VIX 높을 때)?
가설: 지수 저점매수는 '공포일 때', 모멘텀 추세추종은 '평온~추세장일 때' — 정반대일 수 있다.

- 두 전략의 진입 시그널을 캐시 유니버스로 나열, 각 진입일의 VIX/VVIX 수준을 태깅
- VIX 버킷별 forward 수익(모멘텀 63일 / 평균회귀 5일)·승률 비교
  → 각 전략에 'VIX 필터'를 넣는 게 이로운지 판정
실행: python scripts/timing_by_vix.py
"""
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import db  # noqa: E402

CACHE = Path(__file__).resolve().parents[1] / "data" / "us_px_cache.pkl"
COST = 0.0010


def _rsi(s, n):
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def load():
    px, spy = pickle.loads(CACHE.read_bytes())
    px = px.loc[:, px.notna().sum() >= 300]
    con = db.connect()
    vix = pd.Series({r["date"]: r["close"] for r in con.execute(
        "SELECT date, close FROM prices_daily WHERE symbol='^VIX' ORDER BY date")})
    con.close()
    vix.index = pd.to_datetime(vix.index)
    vix = vix.reindex(px.index).ffill()
    return px, spy, vix


def collect_entries(px, spy):
    """모멘텀·평균회귀 진입 (종목,진입일,forward수익) — 진입일 종가 기준."""
    spy63 = spy / spy.shift(63) - 1
    mom, mr = [], []
    for sym in px.columns:
        c = px[sym].dropna()
        if len(c) < 260:
            continue
        ma50 = c.rolling(50).mean()
        ma200 = c.rolling(200).mean()
        rs63 = (c / c.shift(63) - 1) - spy63.reindex(c.index)
        rsi2 = _rsi(c, 2)
        cv, i = c.values, 210
        n = len(c)
        # 모멘텀: rs63 강함 + 추세, 63일 forward
        while i < n - 64:
            if rs63.iloc[i] is not None and rs63.iloc[i] > 0.10 and cv[i] > ma50.iloc[i]:
                mom.append((c.index[i], cv[i + 63] / cv[i] - 1 - COST))
                i += 21
            else:
                i += 1
        # 평균회귀: RSI2 극단 + 장기추세, 5일 forward
        i = 210
        while i < n - 6:
            if rsi2.iloc[i] < 10 and cv[i] > ma200.iloc[i]:
                mr.append((c.index[i], cv[i + 5] / cv[i] - 1 - COST))
                i += 5
            else:
                i += 1
    return mom, mr


def by_vix(entries, vix, label, horizon):
    print(f"\n=== {label} (forward {horizon}일, 진입일 VIX 기준) ===")
    df = pd.DataFrame(entries, columns=["date", "ret"])
    df["vix"] = vix.reindex(df["date"]).values
    df = df.dropna()
    buckets = [("평온 <15", df.vix < 15), ("보통 15~20", (df.vix >= 15) & (df.vix < 20)),
               ("경계 20~30", (df.vix >= 20) & (df.vix < 30)), ("공포 ≥30", df.vix >= 30)]
    print(f"  {'VIX 국면':14}{'N':>7}{'평균수익':>10}{'승률':>8}{'중앙':>9}")
    for name, mask in buckets:
        s = df[mask]
        if len(s) < 20:
            print(f"  {name:14}{len(s):>7}  (표본부족)")
            continue
        print(f"  {name:14}{len(s):>7}{s.ret.mean() * 100:>+9.2f}%{(s.ret > 0).mean():>8.0%}"
              f"{s.ret.median() * 100:>+8.2f}%")
    print(f"  {'전체':14}{len(df):>7}{df.ret.mean() * 100:>+9.2f}%{(df.ret > 0).mean():>8.0%}"
          f"{df.ret.median() * 100:>+8.2f}%")


def main():
    px, spy, vix = load()
    print(f"유니버스 {px.shape[1]}종목 · VIX {vix.min():.1f}~{vix.max():.1f}")
    mom, mr = collect_entries(px, spy)
    by_vix(mom, vix, "모멘텀 추세추종 진입", 63)
    by_vix(mr, vix, "평균회귀(RSI2 급락반등) 진입", 5)
    print("\n해석: 각 전략이 어느 VIX 국면 진입에서 수익·승률이 높은지 → 진입 필터 설계 근거")


if __name__ == "__main__":
    main()
