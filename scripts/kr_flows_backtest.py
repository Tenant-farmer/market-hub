"""KR 외국인 순매수 전환 백테스트 (KOSPI, 약 10년).

가설: "외인이 N일 연속 팔다가 처음 사는 날" = 항복 후 복귀 신호.
룩어헤드 방지: 수급 확정은 당일 저녁 → 신호 다음날 종가 매수로 계산.

- A: 전환 신호 (N일 연속 순매도 → 첫 순매수), N=3/5/10 → +21/63일
- B: 대량 항복 후 전환 (20일 누적 -3조 이하 & 첫 순매수)
- C: 외인 추종 (5일 연속 순매수 중 매수, 중첩) — "외인 따라사기" 검증
- 베이스라인: 전체 일자

실행: .venv\\Scripts\\python scripts\\kr_flows_backtest.py  (KRX 계정 필요)
"""
import sys

import numpy as np
import pandas as pd
import yfinance as yf
from dotenv import load_dotenv

load_dotenv()
from pykrx import stock  # noqa: E402


def stats(r):
    if len(r) < 8:
        return f"표본부족 (n={len(r)})"
    return (f"n={len(r):>4} · 승률 {(r > 0).mean():4.0%} · 중앙값 {np.median(r) * 100:+5.1f}% "
            f"· 평균 {r.mean() * 100:+5.1f}% · 최악 {r.min() * 100:+6.1f}%")


def fwd_from_next(px: pd.Series, dates, h: int) -> np.ndarray:
    """신호일 다음 거래일 종가 진입 → h일 후."""
    out = []
    idx = px.index
    for d in dates:
        i = idx.get_indexer([d], method="backfill")[0]
        j = i + 1
        if j + h < len(px):
            out.append(px.iloc[j + h] / px.iloc[j] - 1)
    return np.array(out)


def main():
    flows = stock.get_market_trading_value_by_date("20150601", "20260716", "KOSPI")
    f = flows["외국인합계"].astype(float)
    print(f"수급 이력: {f.index[0].date()} ~ {f.index[-1].date()} ({len(f)}일)")

    ks = yf.download("^KS11", start="2015-06-01", auto_adjust=True, progress=False)["Close"]
    if isinstance(ks, pd.DataFrame):
        ks = ks.iloc[:, 0]
    ks.index = ks.index.tz_localize(None).normalize()
    f.index = pd.to_datetime(f.index).normalize()
    common = f.index.intersection(ks.index)
    f, px = f.loc[common], ks.loc[common]

    neg = f < 0
    print("\n=== A. 전환 신호: N일 연속 순매도 → 첫 순매수 (다음날 종가 진입) ===")
    for n in (3, 5, 10):
        sig = []
        run = 0
        for i in range(len(f)):
            if f.iloc[i] > 0 and run >= n:
                sig.append(f.index[i])
            run = run + 1 if neg.iloc[i] else 0
        for h, lbl in ((21, "21일"), (63, "63일")):
            print(f"N={n:>2} ({len(sig):>3}회) +{lbl}: {stats(fwd_from_next(px, sig, h))}")
        print()

    print("=== B. 대량 항복 후 전환 (20일 누적 -3조 이하 & 첫 순매수) ===")
    cum20 = f.rolling(20).sum()
    sig = [f.index[i] for i in range(1, len(f))
           if f.iloc[i] > 0 and f.iloc[i - 1] < 0 and cum20.iloc[i - 1] < -3e12]
    for h, lbl in ((21, "21일"), (63, "63일")):
        print(f"({len(sig):>3}회) +{lbl}: {stats(fwd_from_next(px, sig, h))}")

    print("\n=== C. 외인 추종: 5일 연속 순매수 중인 날 매수 (중첩 표본) ===")
    pos5 = (f > 0).rolling(5).sum() == 5
    for h, lbl in ((21, "21일"), (63, "63일")):
        days = list(f.index[pos5])
        print(f"({len(days):>4}일) +{lbl}: {stats(fwd_from_next(px, days, h))}")

    print("\n=== 베이스라인: 전체 일자 ===")
    for h, lbl in ((21, "21일"), (63, "63일")):
        print(f"+{lbl}: {stats(fwd_from_next(px, list(f.index), h))}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
