"""VIX 기반 매수 타이밍 백테스트 (2000~현재, 일봉).

질문: VIX가 어느 수준일 때 사야 이후 승률/수익률이 높은가?

- 대상: SPY (미국), KOSPI ^KS11 (VIX는 전일 미국 종가를 사용 — 룩어헤드 방지)
- 방법 1) VIX 레벨 구간별: 그 날 종가 매수 → H거래일 후 수익률 (중첩 표본 주의)
- 방법 2) 스파이크 에피소드: VIX가 임계값 상향 돌파(비중첩) 시 매수 → 63일 후
- 배당 포함(수정종가), 거래비용 미반영

실행: .venv\\Scripts\\python scripts\\vix_backtest.py
"""
import sys

import numpy as np
import pandas as pd
import yfinance as yf

BUCKETS = [(0, 15), (15, 20), (20, 25), (25, 30), (30, 40), (40, 200)]
HORIZONS = (21, 63, 126)          # 1/3/6개월
SPIKES = ((30, 25), (35, 30), (40, 35))   # (진입 임계, 리셋 임계)


def bucket_table(px: pd.Series, vix: pd.Series, label: str):
    fwd = {h: px.shift(-h) / px - 1 for h in HORIZONS}
    print(f"\n=== {label}: VIX 구간별 매수 성과 ===")
    print(f"{'VIX':>8} {'표본':>6} | " + " | ".join(f"{h}일 승률/중앙값" for h in HORIZONS))
    for lo, hi in BUCKETS:
        mask = (vix >= lo) & (vix < hi) & px.notna()
        cells = []
        for h in HORIZONS:
            r = fwd[h][mask].dropna()
            cells.append(f"{(r > 0).mean():4.0%} {np.median(r) * 100:+5.1f}%" if len(r) > 30 else "  표본부족")
        print(f"{lo:>3}-{hi:<4} {mask.sum():>6} | " + " | ".join(cells))
    base = []
    for h in HORIZONS:
        r = fwd[h][px.notna()].dropna()
        base.append(f"{(r > 0).mean():4.0%} {np.median(r) * 100:+5.1f}%")
    print(f"{'전체':>8} {px.notna().sum():>6} | " + " | ".join(base))


def spike_table(px: pd.Series, vix: pd.Series, label: str, h: int = 63):
    print(f"\n=== {label}: VIX 스파이크 진입 (비중첩 에피소드, +{h}일) ===")
    for enter, reset in SPIKES:
        armed = True
        rets = []
        dates = []
        v = vix.reindex(px.index).ffill()
        for i, (dt, val) in enumerate(v.items()):
            if pd.isna(val):
                continue
            if armed and val >= enter:
                if i + h < len(px) and pd.notna(px.iloc[i]) and pd.notna(px.iloc[i + h]):
                    rets.append(px.iloc[i + h] / px.iloc[i] - 1)
                    dates.append(str(dt.date()))
                armed = False
            elif not armed and val <= reset:
                armed = True
        if rets:
            r = np.array(rets)
            print(f"VIX>={enter:>2}: {len(r):>2}회 · 승률 {(r > 0).mean():4.0%} · "
                  f"중앙값 {np.median(r) * 100:+.1f}% · 평균 {r.mean() * 100:+.1f}% · 최악 {r.min() * 100:+.1f}%")
            print(f"         진입일: {', '.join(dates[:12])}{' ...' if len(dates) > 12 else ''}")


def main():
    raw = yf.download(["SPY", "^VIX", "^KS11"], start="2000-01-01",
                      auto_adjust=True, progress=False)["Close"]
    spy, vix, ks = raw["SPY"].dropna(), raw["^VIX"].dropna(), raw["^KS11"].dropna()
    print(f"데이터: {raw.index[0].date()} ~ {raw.index[-1].date()} "
          f"(SPY {spy.notna().sum()}일, KOSPI {ks.notna().sum()}일)")

    # 미국: 당일 VIX 종가로 당일 SPY 종가 매수 (동시점)
    bucket_table(spy, vix.reindex(spy.index), "SPY")
    spike_table(spy, vix.reindex(spy.index), "SPY")

    # 한국: 전일(미국 기준) VIX만 사용 가능 — shift(1)로 룩어헤드 방지
    vix_kr = vix.shift(1).reindex(ks.index).ffill()
    bucket_table(ks, vix_kr, "KOSPI (전일 VIX)")
    spike_table(ks, vix_kr, "KOSPI (전일 VIX)")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
