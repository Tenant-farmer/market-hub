"""매수 신호등(VIX×VVIX) 전략을 KR 지수 + SPY/QQQ에 적용 — '지수 상시보유 vs 신호 타이밍'.

같은 green 신호(VIX≥30 or VIX≥20&VVIX≥95)로 각 지수를 매수·3/6개월 보유. 벤치마크는 각 지수 단순보유.
KR(코스피·코스닥)은 미국장 마감을 다음 KR 세션에 반영(신호 1일 지연, 룩어헤드 방지). 편도 10bp.
big-picture용: KR 지수 자체가 '살 만한지'(박스피 여부) + SPY vs QQQ 비교를 한 표에.

실행: python scripts/kr_signal_backtest.py
"""
import numpy as np
import pandas as pd
import yfinance as yf

COST = 0.0010


def series_for(idx, lag):
    raw = yf.download([idx, "^VIX", "^VVIX"], start="2006-01-01", auto_adjust=True, progress=False)["Close"]
    raw = raw.dropna(subset=[idx])
    v, w = raw["^VIX"].ffill().values, raw["^VVIX"].ffill().values
    green = (v >= 30) | ((v >= 20) & (w >= 95))
    if lag:
        green = np.concatenate([[False] * lag, green[:len(green) - lag]])
    return raw.index, raw[idx].values, green


def run(dates, px, green, H):
    eq, equity, left, hold, inv, tr = [], 1.0, 0, False, 0, 0
    for i in range(len(px)):
        if i > 0 and hold and not (np.isnan(px[i]) or np.isnan(px[i - 1])):
            equity *= px[i] / px[i - 1]
        if green[i]:
            left = H
        nh = left > 0
        if nh != hold:
            equity *= 1 - COST
            tr += 1 if nh else 0
        hold = nh
        inv += 1 if hold else 0
        left = max(0, left - 1)
        eq.append(equity)
    return pd.Series(eq, index=dates), inv / len(px), tr


def stats(c):
    r = c.pct_change().dropna()
    yrs = (c.index[-1] - c.index[0]).days / 365.25
    return (c.iloc[-1] - 1, c.iloc[-1] ** (1 / yrs) - 1,
            (c / c.cummax() - 1).min(), r.mean() / r.std() * np.sqrt(252) if r.std() else 0)


def block(name, idx, lag):
    dates, px, green = series_for(idx, lag)
    bh = pd.Series(px / px[np.argmax(~np.isnan(px))], index=dates)
    print(f"\n[{name}]  {dates[0].date()}~{dates[-1].date()}  (신호지연 {lag}일)")
    print(f"  {'전략':22}{'총수익':>9}{'CAGR':>8}{'MaxDD':>8}{'샤프':>7}{'투자시간':>8}")
    tr_, cg, dd, sh = stats(bh)
    print(f"  {'단순보유':22}{tr_:>+9.0%}{cg:>+8.1%}{dd:>+8.1%}{sh:>7.2f}{'100%':>8}")
    for H, tag in [(63, "신호→3개월"), (126, "신호→6개월")]:
        eq, inv, _ = run(dates, px, green, H)
        tr_, cg, dd, sh = stats(eq)
        print(f"  {tag:22}{tr_:>+9.0%}{cg:>+8.1%}{dd:>+8.1%}{sh:>7.2f}{inv:>7.0%}")


def main():
    print("데이터 다운로드 (야후, 배당조정)...")
    block("US · SPY", "SPY", 0)
    block("US · QQQ (나스닥100)", "QQQ", 0)
    block("KR · KOSPI", "^KS11", 1)
    block("KR · KOSDAQ", "^KQ11", 1)


if __name__ == "__main__":
    main()
