"""Fear & Greed 매수 타이밍 백테스트 + VIX×VVIX 신호 대비 증분 가치 검증.

- A: F&G 구간별 SPY 매수 → 21/63일
- B: F&G와 VIX의 중복도 (레벨/변화 상관)
- C: 증분 가치 — VIX×VVIX 신호가 '평시'인 날, F&G가 갈라주는가 (다이버전스)
- D: 극단 탐욕(75+)은 회피 신호로 유효한가

실행: .venv\\Scripts\\python scripts\\fng_backtest.py
"""
import sys

import numpy as np
import pandas as pd
import requests
import yfinance as yf

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
H = 63


def stats(r):
    if len(r) < 20:
        return f"표본부족 (n={len(r)})"
    return (f"n={len(r):>4} · 승률 {(r > 0).mean():4.0%} · 중앙값 {np.median(r) * 100:+5.1f}% "
            f"· 평균 {r.mean() * 100:+5.1f}%")


def main():
    hist = None
    for start in ("2015-01-01", "2018-01-01", "2020-01-01", "2020-09-01", "2021-01-01"):
        r = requests.get(
            f"https://production.dataviz.cnn.io/index/fearandgreed/graphdata/{start}",
            headers=UA, timeout=30)
        if r.ok:
            hist = r.json()["fear_and_greed_historical"]["data"]
            break
    if hist is None:
        sys.exit("F&G 이력 엔드포인트 응답 없음")
    fng = pd.Series(
        [d["y"] for d in hist],
        index=pd.to_datetime([d["x"] for d in hist], unit="ms").normalize(),
    ).groupby(level=0).last()
    print(f"F&G 이력: {fng.index[0].date()} ~ {fng.index[-1].date()} ({len(fng)}일)")

    raw = yf.download(["SPY", "^VIX", "^VVIX"], start=str(fng.index[0].date()),
                      auto_adjust=True, progress=False)["Close"]
    spy, vix, vvix = raw["SPY"], raw["^VIX"], raw["^VVIX"]
    fng = fng.reindex(spy.index).ffill(limit=3)
    f21, f63 = spy.shift(-21) / spy - 1, spy.shift(-H) / spy - 1

    print("\n=== A. F&G 구간별 SPY 매수 ===")
    print(f"{'F&G':>8} | 21일                                | 63일")
    for lo, hi, name in [(0, 25, "극단공포"), (25, 45, "공포"), (45, 55, "중립"),
                         (55, 75, "탐욕"), (75, 101, "극단탐욕")]:
        m = (fng >= lo) & (fng < hi)
        a, b = f21[m].dropna().values, f63[m].dropna().values
        print(f"{lo:>3}-{hi:<3} {name:4} | {stats(a):<37} | {stats(b)}")
    print(f"{'전체':>8} | {stats(f21[fng.notna()].dropna().values):<37} | "
          f"{stats(f63[fng.notna()].dropna().values)}")

    print("\n=== B. VIX와의 중복도 ===")
    both = pd.concat([fng, vix], axis=1, keys=["fng", "vix"]).dropna()
    print(f"레벨 상관: {both['fng'].corr(both['vix']):+.2f} · "
          f"일변화 상관: {both['fng'].diff().corr(both['vix'].diff()):+.2f}")

    print("\n=== C. 증분 가치 — VIX×VVIX '평시'(VIX<20 & VVIX<95)인 날을 F&G로 분할 (63일) ===")
    neutral = (vix < 20) & (vvix < 95)
    for cond, label in [(fng < 30, "F&G < 30 (내부지표 공포)"),
                        ((fng >= 30) & (fng < 60), "F&G 30~60"),
                        (fng >= 60, "F&G >= 60 (탐욕)")]:
        print(f"평시 & {label}: {stats(f63[neutral & cond].dropna().values)}")

    print("\n=== D. 극단탐욕(75+)은 회피 신호인가 — VIX<20 평온장 내부 비교 (63일) ===")
    calm = vix < 20
    for cond, label in [(fng >= 75, "F&G 75+"), (fng < 75, "F&G < 75")]:
        print(f"평온장 & {label}: {stats(f63[calm & cond].dropna().values)}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
