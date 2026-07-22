"""통합 전략 백테스트 + ablation — 섹터 로테이션 (진입: 레짐+주도력+과열필터 / 청산: 손절·추세·레짐).

우리 시스템 규칙을 하나의 전략으로 묶어 13년(다국면)에 검증. 룩어헤드 없음, 편도 10bp 비용.
ablation: 규칙을 하나씩 빼며 무엇이 성과에 해로운지 진단 (이기려는 튜닝 아님, 이해 목적).

- 유니버스: SPDR 섹터 11 + 반도체(SMH), 벤치마크 SPY (배당조정 총수익)
- 진입: 월 1회 리밸런스, SPY>200MA(레짐 ON)일 때만, [과열 RSI≥75 제외],
  주도점수(rs_63 .40 + rs_21 .35 + 모멘텀 .25 백분위) 상위 3 섹터 동일비중
- 청산(매일): 손절 -8% / [추세이탈 종가<20MA] / 레짐 OFF → 현금  ([ ] = ablation 대상)

실행: python scripts/strategy_backtest.py
"""
import numpy as np
import pandas as pd
import yfinance as yf

SECTORS = ["XLK", "XLC", "XLY", "XLP", "XLE", "XLF", "XLV", "XLI", "XLB", "XLRE", "XLU", "SMH"]
ALL = ["SPY"] + SECTORS
START, TARGET_N, STOP, COST, OVERHEAT_RSI = "2012-01-01", 3, -0.08, 0.0010, 75.0


def rsi(s, n=14):
    d = s.diff()
    up, dn = d.clip(lower=0).rolling(n).mean(), (-d.clip(upper=0)).rolling(n).mean()
    return 100 - 100 / (1 + up / dn)


def load():
    px = yf.download(ALL, start=START, auto_adjust=True, progress=False)["Close"].dropna(how="all")
    ret63, ret21 = px / px.shift(63) - 1, px / px.shift(21) - 1
    A = {  # 넘파이 배열로 (루프 속도)
        "px": {s: px[s].values for s in ALL},
        "ma20": {s: px[s].rolling(20).mean().values for s in SECTORS},
        "rsi": {s: rsi(px[s]).values for s in SECTORS},
        "rs63": {s: ret63[s].sub(ret63["SPY"]).values for s in SECTORS},
        "rs21": {s: ret21[s].sub(ret21["SPY"]).values for s in SECTORS},
        "spy200": px["SPY"].rolling(200).mean().values,
    }
    A["mom"] = {s: A["rs63"][s] - np.concatenate([[np.nan] * 21, A["rs63"][s][:-21]]) for s in SECTORS}
    return px.index, A


def run(dates, A, ma_exit=True, overheat=True):
    px, spy = A["px"], A["px"]["SPY"]
    month = pd.Series(pd.DatetimeIndex(dates).to_period("M"))
    is_rebal = month.ne(month.shift(1)).values
    holdings, equity, eq, inv = {}, 1.0, [], 0
    for i in range(len(dates)):
        if i > 0 and holdings:
            equity *= 1 + sum((px[s][i] / px[s][i - 1] - 1) / TARGET_N for s in holdings)
        cost = 0.0
        regime_off = not (spy[i] >= A["spy200"][i]) if not np.isnan(A["spy200"][i]) else True
        for s in list(holdings):                      # 매일 청산
            hit = (regime_off or px[s][i] / holdings[s] - 1 <= STOP
                   or (ma_exit and not np.isnan(A["ma20"][s][i]) and px[s][i] < A["ma20"][s][i]))
            if hit:
                cost += COST / TARGET_N
                del holdings[s]
        if is_rebal[i]:                               # 월별 리밸런스
            target = []
            if not regime_off:
                cand = {}
                for s in SECTORS:
                    if any(np.isnan(A[k][s][i]) for k in ("rs63", "rs21", "mom")):
                        continue
                    if overheat and not np.isnan(A["rsi"][s][i]) and A["rsi"][s][i] >= OVERHEAT_RSI:
                        continue
                    cand[s] = (A["rs63"][s][i], A["rs21"][s][i], A["mom"][s][i])
                if cand:
                    df = pd.DataFrame(cand, index=["rs63", "rs21", "mom"]).T
                    rank = (df.rank(pct=True) * [0.40, 0.35, 0.25]).sum(axis=1)
                    target = list(rank.sort_values(ascending=False).head(TARGET_N).index)
            new, old = set(target), set(holdings)
            for s in old - new:
                cost += COST / TARGET_N
                del holdings[s]
            for s in new - old:
                cost += COST / TARGET_N
                holdings[s] = px[s][i]
            for s in holdings:
                holdings[s] = px[s][i]
        equity *= 1 - cost
        eq.append(equity)
        inv += 1 if holdings else 0
    return pd.Series(eq, index=dates), inv / len(dates)


def stats(c):
    r = c.pct_change().dropna()
    yrs = (c.index[-1] - c.index[0]).days / 365.25
    return (c.iloc[-1] - 1, c.iloc[-1] ** (1 / yrs) - 1,
            (c / c.cummax() - 1).min(), r.mean() / r.std() * np.sqrt(252) if r.std() else 0)


def main():
    print("데이터 다운로드 (야후, 배당조정 13년)...")
    dates, A = load()
    bh = A["px"]["SPY"] / A["px"]["SPY"][0]
    bh = pd.Series(bh, index=dates)

    configs = [
        ("① 전규칙 (baseline)", dict(ma_exit=True, overheat=True)),
        ("② MA청산 제거", dict(ma_exit=False, overheat=True)),
        ("③ 과열필터 제거", dict(ma_exit=True, overheat=False)),
        ("④ MA+과열 제거", dict(ma_exit=False, overheat=False)),
    ]
    print(f"\n{'전략':22}{'총수익':>9}{'CAGR':>8}{'MaxDD':>8}{'샤프':>7}{'투자시간':>8}")
    tr, cg, dd, sh = stats(bh)
    print(f"{'SPY 단순보유':22}{tr:>+9.0%}{cg:>+8.1%}{dd:>+8.1%}{sh:>7.2f}{'100%':>8}")
    for name, cfg in configs:
        eq, inv = run(dates, A, **cfg)
        tr, cg, dd, sh = stats(eq)
        print(f"{name:22}{tr:>+9.0%}{cg:>+8.1%}{dd:>+8.1%}{sh:>7.2f}{inv:>7.0%}")


if __name__ == "__main__":
    main()
