"""VIX × VVIX 결합 매수 타이밍 백테스트 (2007~현재, ^VVIX 가용 구간).

- Test A: VVIX 단독 구간별 → 21/63/126일 성과
- Test B: VIX×VVIX 2차원 그리드 → 63일 성과 (조건부 개선 확인)
- Test C: VIX 스파이크 진입을 "VVIX 냉각 여부"로 이분 — 핵심 실전 규칙 후보
- Test D: VVIX/VIX 비율 5분위 → 63일
- KOSPI: Test C 재현 (전일 지표 사용, 룩어헤드 방지)

실행: .venv\\Scripts\\python scripts\\vvix_backtest.py
"""
import sys

import numpy as np
import pandas as pd
import yfinance as yf

H = 63


def stats(r: np.ndarray) -> str:
    if len(r) == 0:
        return "표본 없음"
    return (f"n={len(r):>4} · 승률 {(r > 0).mean():4.0%} · 중앙값 {np.median(r) * 100:+5.1f}% "
            f"· 평균 {r.mean() * 100:+5.1f}% · 최악 {r.min() * 100:+6.1f}%")


def fwd(px: pd.Series, h: int) -> pd.Series:
    return px.shift(-h) / px - 1


def episodes(px, vix, vvix, enter, reset):
    """VIX 스파이크 진입(비중첩)을 VVIX 냉각 여부로 분류."""
    vvix5 = vvix.rolling(5).mean()
    armed, out = True, {"cool": [], "hot": []}
    idx = px.index
    for i, dt in enumerate(idx):
        v = vix.get(dt)
        if pd.isna(v):
            continue
        if armed and v >= enter:
            if i + H < len(idx) and pd.notna(px.iloc[i]) and pd.notna(px.iloc[i + H]):
                ret = px.iloc[i + H] / px.iloc[i] - 1
                cooling = pd.notna(vvix.get(dt)) and pd.notna(vvix5.get(dt)) and vvix[dt] < vvix5[dt]
                out["cool" if cooling else "hot"].append((str(dt.date()), ret))
            armed = False
        elif not armed and v <= reset:
            armed = True
    return out


def main():
    raw = yf.download(["SPY", "^VIX", "^VVIX", "^KS11"], start="2006-06-01",
                      auto_adjust=True, progress=False)["Close"]
    spy, vix, vvix, ks = raw["SPY"], raw["^VIX"], raw["^VVIX"], raw["^KS11"]
    ok = vvix.notna()
    print(f"VVIX 가용: {vvix.dropna().index[0].date()} ~ {vvix.dropna().index[-1].date()} ({ok.sum()}일)")

    # A. VVIX 단독 구간
    print("\n=== A. VVIX 구간별 SPY 매수 (63일) ===")
    for lo, hi in [(0, 85), (85, 95), (95, 105), (105, 120), (120, 999)]:
        m = (vvix >= lo) & (vvix < hi)
        r = fwd(spy, H)[m].dropna().values
        print(f"VVIX {lo:>3}-{hi:<3}: {stats(r)}")

    # B. VIX × VVIX 그리드
    print("\n=== B. VIX × VVIX 그리드 — SPY 63일 승률/중앙값 (n) ===")
    vix_b = [(0, 20), (20, 30), (30, 999)]
    vvix_b = [(0, 95), (95, 110), (110, 999)]
    hdr = " " * 12 + " | ".join(f"VVIX {lo}-{hi if hi < 999 else '+'}".ljust(20) for lo, hi in vvix_b)
    print(hdr)
    f63 = fwd(spy, H)
    for vlo, vhi in vix_b:
        cells = []
        for wlo, whi in vvix_b:
            m = (vix >= vlo) & (vix < vhi) & (vvix >= wlo) & (vvix < whi)
            r = f63[m].dropna().values
            cells.append(f"{(r > 0).mean():4.0%} {np.median(r) * 100:+5.1f}% ({len(r):>4})"
                         if len(r) > 30 else f"   표본부족 ({len(r):>3})")
        print(f"VIX {vlo:>2}-{'+' if vhi > 100 else vhi:<3}  " + " | ".join(c.ljust(20) for c in cells))

    # C. 고VIX 국면에서 VVIX 냉각 여부 (일 단위 — 스파이크 최초 돌파일엔 VVIX가 항상 상승 중이라
    #    에피소드 방식으론 냉각 표본이 없음. 중첩 표본 주의)
    print("\n=== C. VIX 고공 국면 × VVIX 냉각(5일평균 아래) — SPY +63일, 일 단위 ===")
    vvix5 = vvix.rolling(5).mean()
    cooling = vvix < vvix5
    for lo in (25, 30, 35):
        m_hi = vix >= lo
        for cond, label in ((cooling, "VVIX 냉각중"), (~cooling, "VVIX 상승중")):
            r = f63[m_hi & cond].dropna().values
            print(f"VIX>={lo} & {label}: {stats(r)}")
        print()

    # D. VVIX/VIX 비율 5분위
    print("=== D. VVIX/VIX 비율 5분위 — SPY 63일 ===")
    ratio = (vvix / vix).dropna()
    qs = ratio.quantile([0.2, 0.4, 0.6, 0.8])
    bounds = [-np.inf, *qs.values, np.inf]
    for i in range(5):
        m = (ratio > bounds[i]) & (ratio <= bounds[i + 1])
        r = f63[m.reindex(spy.index, fill_value=False)].dropna().values
        lo = f"{bounds[i]:.1f}" if np.isfinite(bounds[i]) else "min"
        hi = f"{bounds[i + 1]:.1f}" if np.isfinite(bounds[i + 1]) else "max"
        print(f"Q{i + 1} ({lo}~{hi}): {stats(r)}")

    # KOSPI: 전일 지표로 Test C
    print("\n=== KOSPI: VIX 스파이크 × VVIX 냉각 (전일 지표, +63일) ===")
    vix_l = vix.shift(1).reindex(ks.index).ffill()
    vvix_l = vvix.shift(1).reindex(ks.index).ffill()
    ep = episodes(ks, vix_l, vvix_l, 30, 25)
    for k, label in (("cool", "VVIX 냉각중"), ("hot", "VVIX 상승중")):
        r = np.array([x[1] for x in ep[k]])
        print(f"VIX>=30 & {label}: {stats(r)}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
