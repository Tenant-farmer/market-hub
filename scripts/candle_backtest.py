"""캔들 패턴 예측력 검증 — 신호로 쓸 가치가 있나.

우리 원칙: 표시하기 전에 검증한다. 각 패턴 감지 다음날 진입 → N일 수익(시장초과)을
전체 평균과 비교해 **패턴이 실제로 예측력이 있는지** 본다.

기대: 대부분 무의미할 것(6전략 백테스트에서 패턴 계열이 SPY에 짐). 그래도 숫자를 내서
UI에 '이 패턴의 과거 성적'을 정직하게 함께 표시한다 — 사용자가 맹신하지 않게.

실행: python scripts/candle_backtest.py
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from src.analytics.candles import DIRECTION, detect  # noqa: E402

HORIZONS = (5, 21)


def load_ohlc():
    """DB에서 US 개별종목 OHLC (캐시는 종가만이라 DB 사용)."""
    from src import db

    con = db.connect()
    rows = con.execute(
        "SELECT symbol, date, open, high, low, close FROM prices_daily "
        "WHERE market='US_STOCK' AND open IS NOT NULL ORDER BY symbol, date").fetchall()
    con.close()
    df = pd.DataFrame([dict(r) for r in rows])
    if df.empty:
        sys.exit("[중단] OHLC 데이터 없음")
    return df


def main():
    df = load_ohlc()
    syms = df["symbol"].unique()
    print(f"US 종목 {len(syms)}개 · {df['date'].min()}~{df['date'].max()}")

    # 벤치마크: SPY (동일가중은 DB NaN 79.5%로 왜곡됨 — 신규상장 첫 수익률이 평균을 부풀림)
    from src import db as _db

    con = _db.connect()
    spy_rows = con.execute("SELECT date, close FROM prices_daily WHERE symbol='SPY' "
                           "ORDER BY date").fetchall()
    con.close()
    spy = {r["date"]: r["close"] for r in spy_rows}      # date(str) → close
    spy_dates = sorted(spy)
    if len(spy) < 100:
        sys.exit("[중단] SPY 벤치마크 데이터 부족")

    def spy_at(d):                                      # 해당일 이하 최근 SPY 종가
        import bisect
        i = bisect.bisect_right(spy_dates, d) - 1
        return spy[spy_dates[i]] if i >= 0 else None

    recs = []
    for sym, g in df.groupby("symbol"):
        g = g.set_index("date")
        if len(g) < 40:
            continue
        flags = detect(g)
        if flags.empty:
            continue
        c = g["close"]
        for name in flags.columns:
            idx = np.where(flags[name].values)[0]
            for i in idx:
                if i + 1 >= len(c) or i + 1 + max(HORIZONS) >= len(c):
                    continue
                base = c.iloc[i + 1]                       # 다음날 종가 진입(룩어헤드 차단)
                d0 = c.index[i + 1]
                rec = {"pattern": name, "dir": DIRECTION.get(name)}
                ok = True
                s0 = spy_at(d0)
                for H in HORIZONS:
                    s1 = spy_at(c.index[i + 1 + H])
                    if not s0 or not s1:
                        ok = False
                        break
                    rec[f"a{H}"] = (c.iloc[i + 1 + H] / base - 1) - (s1 / s0 - 1)
                if ok:
                    recs.append(rec)
    d = pd.DataFrame(recs)
    if d.empty:
        sys.exit("[중단] 감지된 패턴 없음")
    print(f"패턴 발생 총 {len(d):,}건\n")

    base_a5 = d[[c for c in d.columns if c == "a5"]].mean().iloc[0]
    print(f"{'패턴':12}{'방향':6}{'N':>7}{'+5일초과':>10}{'승률':>7}{'+21일초과':>11}{'승률':>7}{'판정':>8}")
    for name, g in d.groupby("pattern"):
        if len(g) < 30:
            print(f"{name:12}{'':6}{len(g):>7}  (표본부족)")
            continue
        a5, a21 = g["a5"].mean(), g["a21"].mean()
        w5, w21 = (g["a5"] > 0).mean(), (g["a21"] > 0).mean()
        dirn = DIRECTION.get(name, "")
        # 방향과 실제 수익이 일치하는지 (bull인데 초과수익 양수면 적중)
        hit = (a21 > 0) if dirn == "bull" else (a21 < 0) if dirn == "bear" else None
        verdict = ("적중" if hit else "반대" if hit is False else "중립") if hit is not None else "중립"
        # 유의성: 초과수익이 0.5%p 넘고 표본 100+ 일 때만 의미 부여
        if abs(a21) < 0.005 or len(g) < 100:
            verdict = "미미"
        print(f"{name:12}{dirn:6}{len(g):>7}{a5*100:>+9.2f}%{w5:>7.0%}"
              f"{a21*100:>+10.2f}%{w21:>7.0%}{verdict:>8}")
    print("\n※ '미미' = 초과수익 0.5%p 미만 또는 표본 100건 미만 — 신호로 쓸 가치 없음")
    print("※ 이 결과를 종목 상세 UI에 함께 표시해 맹신을 방지한다")


if __name__ == "__main__":
    main()
