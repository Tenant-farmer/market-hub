"""매수 신호등(VIX×VVIX) 기반 전략 백테스트.

개요 페이지의 '매수 신호등'(queries_macro.classify_vix_signal)이 매수(green)일 때 진입하는
두 전략을 검증. green ⟺ VIX≥30  또는  (VIX≥20 & VVIX≥95)  — 공포 급등을 되받는 역발상 신호.

 ① 신호등 → SPY 매수 (타이밍만)
 ② 신호등 → 주도 섹터 top3 매수 (타이밍 + 주도주 선정, leader_score 상위 3)
진입 후 H거래일 보유(그 사이 재점등 시 보유시계 리셋), 만료 시 현금. 벤치마크: SPY 단순보유.
룩어헤드 없음(신호·주도점수 모두 당일 종가까지만), 편도 10bp. VVIX 되는 최대구간(≈2007~).

실행: python scripts/signal_backtest.py
"""
import numpy as np
import pandas as pd
import yfinance as yf

SECTORS = ["XLK", "XLC", "XLY", "XLP", "XLE", "XLF", "XLV", "XLI", "XLB", "XLRE", "XLU", "SMH"]
ALL = ["SPY"] + SECTORS
START, COST, TARGET_N, W = "2006-01-01", 0.0010, 3, [0.40, 0.35, 0.25]


def load():
    px = yf.download(ALL + ["^VIX", "^VVIX"], start=START, auto_adjust=True, progress=False)["Close"]
    px = px.dropna(subset=["SPY", "^VIX", "^VVIX"])          # 신호 계산 가능한 구간만
    green = (px["^VIX"].values >= 30) | ((px["^VIX"].values >= 20) & (px["^VVIX"].values >= 95))
    r63, r21 = px / px.shift(63) - 1, px / px.shift(21) - 1
    A = {"spy": px["SPY"].values, "px": {}, "rs63": {}, "rs21": {}, "mom": {}}
    for s in SECTORS:
        a, b = (r63[s] - r63["SPY"]).values, (r21[s] - r21["SPY"]).values
        A["px"][s], A["rs63"][s], A["rs21"][s] = px[s].values, a, b
        A["mom"][s] = a - np.concatenate([[np.nan] * 21, a[:-21]])
    return px.index, green, A


def pick_leaders(A, i):
    cand = {s: (A["rs63"][s][i], A["rs21"][s][i], A["mom"][s][i]) for s in SECTORS
            if not any(np.isnan(v) for v in (A["rs63"][s][i], A["rs21"][s][i], A["mom"][s][i]))}
    if not cand:
        return []
    d = pd.DataFrame(cand, index=["rs63", "rs21", "mom"]).T
    rank = (d.rank(pct=True) * W).sum(axis=1)
    return list(rank.sort_values(ascending=False).head(TARGET_N).index)


def run_spy(dates, green, spy, H):
    eq, equity, left, hold, inv, tr = [], 1.0, 0, False, 0, 0
    for i in range(len(spy)):
        if i > 0 and hold:
            equity *= spy[i] / spy[i - 1]
        if green[i]:
            left = H
        nh = left > 0
        if nh != hold:                        # 진입/청산 비용
            equity *= 1 - COST
            tr += 1 if nh else 0
        hold = nh
        inv += 1 if hold else 0
        left = max(0, left - 1)
        eq.append(equity)
    return pd.Series(eq, index=dates), inv / len(spy), tr


def run_leaders(dates, green, A, H):
    px, spy = A["px"], A["spy"]
    eq, equity, holds, left, inv, tr = [], 1.0, [], 0, 0, 0
    for i in range(len(spy)):
        if i > 0 and holds:
            equity *= 1 + sum(px[s][i] / px[s][i - 1] - 1 for s in holds) / len(holds)
        cost = 0.0
        if green[i]:
            tgt = pick_leaders(A, i)
            if tgt:
                cost += COST * len(set(holds) ^ set(tgt)) / TARGET_N
                holds, left, tr = tgt, H, tr + 1
        if holds and left <= 0:               # 보유 만료 → 청산
            cost += COST * len(holds) / TARGET_N
            holds = []
        equity *= 1 - cost
        inv += 1 if holds else 0
        left = max(0, left - 1)
        eq.append(equity)
    return pd.Series(eq, index=dates), inv / len(spy), tr


def stats(c):
    r = c.pct_change().dropna()
    yrs = (c.index[-1] - c.index[0]).days / 365.25
    return (c.iloc[-1] - 1, c.iloc[-1] ** (1 / yrs) - 1,
            (c / c.cummax() - 1).min(), r.mean() / r.std() * np.sqrt(252) if r.std() else 0)


def main():
    print("데이터 다운로드 (야후, 배당조정 + VIX/VVIX)...")
    dates, green, A = load()
    spy = A["spy"]
    n, ng = len(dates), int(green.sum())
    episodes = int((green & ~np.concatenate([[False], green[:-1]])).sum())
    print(f"구간: {dates[0].date()} ~ {dates[-1].date()} ({n}거래일, {(dates[-1]-dates[0]).days/365.25:.1f}년)")
    print(f"매수 신호등 green: {ng}일 ({ng/n:.0%}) · 발생 에피소드 {episodes}회")

    # 신호 forward 수익 검증 (신호 다음 63거래일 SPY 수익 중앙값 vs 무조건)
    fwd = np.array([spy[i + 63] / spy[i] - 1 for i in range(n - 63)])
    g = green[:n - 63]
    print(f"63일 forward SPY 수익 중앙값 / green후 {np.median(fwd[g]):+.1%} vs 평시 {np.median(fwd[~g]):+.1%}\n")

    bh = pd.Series(spy / spy[0], index=dates)
    rows = [("SPY 단순보유", bh, 1.0, 0)]
    for H, tag in [(63, "3개월"), (126, "6개월")]:
        rows.append((f"① 신호등→SPY ({tag})", *run_spy(dates, green, spy, H)))
    for H, tag in [(63, "3개월"), (126, "6개월")]:
        rows.append((f"② 신호등→주도섹터 ({tag})", *run_leaders(dates, green, A, H)))

    print(f"{'전략':26}{'총수익':>9}{'CAGR':>8}{'MaxDD':>8}{'샤프':>7}{'투자시간':>8}{'진입':>6}")
    for name, eq, inv, tr in rows:
        tr_, cg, dd, sh = stats(eq)
        print(f"{name:26}{tr_:>+9.0%}{cg:>+8.1%}{dd:>+8.1%}{sh:>7.2f}{inv:>7.0%}{tr:>6}")


if __name__ == "__main__":
    main()
