"""급등 테마주 추격의 기대값 — "1개월 +100% 급등한 KR 종목을 다음날 사면?"

이벤트: 21일 수익률이 임계값을 상향 돌파한 날 (종목별 21일 쿨다운, 비중첩)
진입: 신호 다음 거래일 종가 (룩어헤드 방지) → +21/63일
표본: 우리 DB의 KOSPI+KOSDAQ 전종목 (~252거래일 — 한 레짐 캐비앗)

실행: .venv\\Scripts\\python scripts\\theme_chase_backtest.py
"""
import sys

import numpy as np

sys.path.insert(0, r".")
from src import db
from src.analytics.data import load_field


def stats(r):
    if len(r) < 10:
        return f"표본부족 (n={len(r)})"
    r = np.array(r)
    return (f"n={len(r):>4} · 승률 {(r > 0).mean():4.0%} · 중앙값 {np.median(r) * 100:+6.1f}% "
            f"· 평균 {r.mean() * 100:+6.1f}% · 최악 {r.min() * 100:+.0f}% · 상위10% {np.percentile(r, 90) * 100:+.0f}%")


def main():
    con = db.connect()
    stocks = [r["symbol"] for r in con.execute(
        "SELECT DISTINCT symbol FROM prices_daily WHERE market='KR'")]
    px = load_field(con, stocks, "close")
    print(f"유니버스 {px.shape[1]}종목 × {px.shape[0]}일 ({px.index[0]} ~ {px.index[-1]})")

    ret21 = px.pct_change(21, fill_method=None)
    arr = px.values
    r21 = ret21.values
    n_days, n_stocks = arr.shape

    for thr, label in ((1.0, "+100% 돌파"), (0.5, "+50% 돌파(참고)")):
        ev21, ev63 = [], []
        n_events = 0
        for s in range(n_stocks):
            cooldown = -99
            for i in range(1, n_days):
                v, p = r21[i, s], r21[i - 1, s]
                if np.isnan(v) or np.isnan(p):
                    continue
                if v >= thr and p < thr and i - cooldown > 21:
                    cooldown = i
                    j = i + 1
                    if j < n_days and not np.isnan(arr[j, s]):
                        n_events += 1
                        for h, bucket in ((21, ev21), (63, ev63)):
                            if j + h < n_days and not np.isnan(arr[j + h, s]):
                                bucket.append(arr[j + h, s] / arr[j, s] - 1)
        print(f"\n=== {label} 추격 매수 (이벤트 {n_events}회) ===")
        print(f"+21일: {stats(ev21)}")
        print(f"+63일: {stats(ev63)}")

    # 베이스라인: 모든 종목·일자
    fwd63 = (px.shift(-63) / px - 1).values.ravel()
    fwd63 = fwd63[~np.isnan(fwd63)]
    fwd21 = (px.shift(-21) / px - 1).values.ravel()
    fwd21 = fwd21[~np.isnan(fwd21)]
    print("\n=== 베이스라인 (전 종목·전 일자) ===")
    print(f"+21일: {stats(fwd21)}")
    print(f"+63일: {stats(fwd63)}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
