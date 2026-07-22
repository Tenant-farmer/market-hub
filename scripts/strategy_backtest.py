"""통합 전략 백테스트 — 섹터 로테이션 (진입: 레짐+주도력+과열필터 / 청산: 손절·추세이탈·레짐).

우리 시스템 규칙을 하나의 전략으로 묶어 13년(다국면)에 검증한다. 룩어헤드 없음:
매일 어제 보유분에 오늘 수익률 적용 → 오늘 종가로 청산·리밸런스 결정(내일 반영).

- 유니버스: SPDR 섹터 11 + 반도체(SMH), 벤치마크 SPY (배당조정 총수익)
- 진입: 월 1회 리밸런스. SPY>200MA(레짐 ON)일 때만, 과열(RSI≥75) 제외,
  주도점수(rs_63 .40 + rs_21 .35 + 모멘텀 .25 백분위) 상위 3 섹터 동일비중(각 1/3)
- 청산(매일): 손절 -8%(리밸 기준가 대비) / 추세이탈(종가<20MA) / 레짐 OFF(SPY<200MA) → 현금
- 비용: 편도 10bp (스프레드+슬리피지)

실행: python scripts/strategy_backtest.py
"""
import numpy as np
import pandas as pd
import yfinance as yf

SECTORS = ["XLK", "XLC", "XLY", "XLP", "XLE", "XLF", "XLV", "XLI", "XLB", "XLRE", "XLU", "SMH"]
ALL = ["SPY"] + SECTORS
START = "2012-01-01"
TARGET_N = 3          # 보유 슬롯 (각 1/TARGET_N 비중)
STOP = -0.08          # 손절
COST = 0.0010         # 편도 거래비용 (10bp)
OVERHEAT_RSI = 75.0


def rsi(s, n=14):
    d = s.diff()
    up = d.clip(lower=0).rolling(n).mean()
    dn = (-d.clip(upper=0)).rolling(n).mean()
    return 100 - 100 / (1 + up / dn)


def main():
    print("데이터 다운로드 (야후, 배당조정 13년)...")
    px = yf.download(ALL, start=START, auto_adjust=True, progress=False)["Close"]
    px = px.dropna(how="all")
    dates = px.index
    spy = px["SPY"]
    spy200 = spy.rolling(200).mean()

    ma20 = {s: px[s].rolling(20).mean() for s in SECTORS}
    rsis = {s: rsi(px[s]) for s in SECTORS}
    ret63, ret21 = px / px.shift(63) - 1, px / px.shift(21) - 1
    rs63 = ret63[SECTORS].sub(ret63["SPY"], axis=0)
    rs21 = ret21[SECTORS].sub(ret21["SPY"], axis=0)
    mom = rs63 - rs63.shift(21)

    month = pd.Series(dates.to_period("M"), index=dates)
    is_rebal = month.ne(month.shift(1))

    holdings = {}         # sym -> 기준가(리밸 시점)
    equity, eq, invested = 1.0, [], 0
    for i, d in enumerate(dates):
        # 1) 어제 보유분에 오늘 수익률 (각 1/TARGET_N 비중)
        if i > 0 and holdings:
            r = sum((px[s].iloc[i] / px[s].iloc[i - 1] - 1) / TARGET_N for s in holdings)
            equity *= 1 + r
        cost = 0.0
        regime_off = not (spy.iloc[i] >= spy200.iloc[i]) if not np.isnan(spy200.iloc[i]) else True

        # 2) 매일 청산 규칙 (오늘 종가 기준)
        for s in list(holdings):
            entry = holdings[s]
            hit = (regime_off
                   or px[s].iloc[i] / entry - 1 <= STOP
                   or (not np.isnan(ma20[s].iloc[i]) and px[s].iloc[i] < ma20[s].iloc[i]))
            if hit:
                cost += COST / TARGET_N
                del holdings[s]

        # 3) 월별 리밸런스 (레짐 ON일 때만 주도 3섹터)
        if is_rebal.iloc[i]:
            target = []
            if not regime_off:
                cand = {}
                for s in SECTORS:
                    if any(np.isnan(x.iloc[i]) for x in (rs63[s], rs21[s], mom[s])):
                        continue
                    if not np.isnan(rsis[s].iloc[i]) and rsis[s].iloc[i] >= OVERHEAT_RSI:
                        continue
                    cand[s] = (rs63[s].iloc[i], rs21[s].iloc[i], mom[s].iloc[i])
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
                holdings[s] = px[s].iloc[i]
            for s in holdings:          # 유지분도 기준가 갱신(월 단위 손절 기준)
                holdings[s] = px[s].iloc[i]

        equity *= 1 - cost
        eq.append(equity)
        invested += 1 if holdings else 0

    eq = pd.Series(eq, index=dates)
    bh = spy / spy.iloc[0]
    _report(eq, bh, invested / len(dates))


def _stats(curve):
    ret = curve.pct_change().dropna()
    yrs = (curve.index[-1] - curve.index[0]).days / 365.25
    cagr = curve.iloc[-1] ** (1 / yrs) - 1
    dd = (curve / curve.cummax() - 1).min()
    vol = ret.std() * np.sqrt(252)
    sharpe = ret.mean() / ret.std() * np.sqrt(252) if ret.std() else 0
    return curve.iloc[-1] - 1, cagr, dd, vol, sharpe


def _report(eq, bh, inv):
    print(f"\n기간: {eq.index[0].date()} ~ {eq.index[-1].date()} ({len(eq)}일) · 투자비중 시간 {inv:.0%}")
    print(f"{'':10}{'총수익':>10}{'CAGR':>9}{'MaxDD':>9}{'변동성':>9}{'샤프':>7}")
    for name, c in (("전략", eq), ("SPY 보유", bh)):
        tr, cagr, dd, vol, sh = _stats(c)
        print(f"{name:10}{tr:>+9.0%}{cagr:>+9.1%}{dd:>+9.1%}{vol:>9.1%}{sh:>7.2f}")

    print("\n연도별 수익 (전략 / SPY):")
    ey = eq.resample("YE").last().pct_change()
    ey.iloc[0] = eq.resample("YE").last().iloc[0] - 1
    by = bh.resample("YE").last().pct_change()
    by.iloc[0] = bh.resample("YE").last().iloc[0] - 1
    for dt in ey.index:
        e, b = ey.loc[dt], by.loc[dt]
        flag = "  ✓방어" if (e > b and b < 0) else ("  ✓초과" if e > b else "")
        print(f"  {dt.year}: {e:>+7.1%}  /  {b:>+7.1%}{flag}")

    # 월간 샘플 (차트용)
    m = eq.resample("ME").last()
    mb = bh.resample("ME").last()
    print("\nEQUITY_JSON " + pd.DataFrame({"d": m.index.strftime("%Y-%m"),
          "strat": m.round(3).values, "spy": mb.round(3).values}).to_json(orient="records"))


if __name__ == "__main__":
    main()
