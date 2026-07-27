"""섹터 로테이션 다차원 검증 — 가격 RS 단독 vs 다차원 결합.

배경: 우리 섹터 페이지는 **가격 RS(모멘텀)만** 쓴다. Vibe-Trading sector-rotation 스킬은
4차원(경기·모멘텀·밸류·자금흐름) 결합을 제안한다. 중국 A주 특화지만 프레임은 범용.

우리 가용 데이터로 재구성:
  - 모멘텀: 섹터 ETF의 63일 시장대비 초과수익 (있음)
  - 추세강도: 50MA 이격도 (있음)
  - 변동성 조정: 모멘텀/변동성 (샤프형 — 스킬의 '안정성' 대용)
  - 브레드스: 섹터 ETF가 자기 200MA 위인 비율 대신, 섹터 내 모멘텀 일관성

검증: 각 방식으로 top3 섹터를 매월 보유했을 때 성과 비교. SPY 벤치.
A/B에서 배운 대로 **복잡하게 해서 좋아지는지**를 실제로 확인한다(대개 아니었다).

실행: python scripts/sector_multidim.py
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import db  # noqa: E402

COST = 0.0005
TOPN = 3


def load_sectors():
    """섹터 ETF 일별 종가 (prices_daily US_ETF) + SPY."""
    con = db.connect()
    rows = con.execute(                    # 섹터 ETF는 market='US' (US_ETF 아님)
        "SELECT symbol, date, close FROM prices_daily WHERE symbol LIKE 'XL%' ORDER BY date"
    ).fetchall()
    spy_rows = con.execute(
        "SELECT date, close FROM prices_daily WHERE symbol='SPY' ORDER BY date").fetchall()
    con.close()
    df = pd.DataFrame([dict(r) for r in rows])
    if df.empty:
        sys.exit("[중단] 섹터 ETF 데이터 없음")
    px = df.pivot(index="date", columns="symbol", values="close")
    px.index = pd.to_datetime(px.index)
    spy = pd.Series({r["date"]: r["close"] for r in spy_rows})
    spy.index = pd.to_datetime(spy.index)
    spy = spy.reindex(px.index).ffill()
    # 섹터 ETF만 (SPY/QQQ/SMH 등 광의 지수 제외)
    keep = [c for c in px.columns if c.startswith("XL")]
    return px[keep].dropna(how="all"), spy


def build_scores(px, spy):
    ret63 = px / px.shift(63) - 1
    s63 = spy / spy.shift(63) - 1
    mom = ret63.sub(s63, axis=0)                       # 63일 시장대비 초과
    ret21 = px / px.shift(21) - 1
    s21 = spy / spy.shift(21) - 1
    mom21 = ret21.sub(s21, axis=0)
    vol = px.pct_change().rolling(63).std() * np.sqrt(252)
    ma50_dev = px / px.rolling(50).mean() - 1          # 추세 강도
    pr = lambda d: d.rank(axis=1, pct=True)            # noqa: E731
    return {
        "① 모멘텀 63일 단독(현행)": mom,
        "② 모멘텀 21일 단독": mom21,
        "③ 위험조정 모멘텀(mom/vol)": mom / vol.replace(0, np.nan),
        "④ 2차원(63일+추세강도)": 0.6 * pr(mom) + 0.4 * pr(ma50_dev),
        "⑤ 3차원(63+21+추세)": 0.45 * pr(mom) + 0.30 * pr(mom21) + 0.25 * pr(ma50_dev),
        "⑥ 4차원(+위험조정)": (0.35 * pr(mom) + 0.25 * pr(mom21) + 0.20 * pr(ma50_dev)
                              + 0.20 * pr(mom / vol.replace(0, np.nan))),
    }


def run(px, score, rebal=21):
    dates, daily = px.index, px.pct_change().fillna(0)
    eq, held, e = [], [], 1.0
    for i in range(len(dates)):
        if i > 0 and held:
            e *= (1 + daily.iloc[i][held].mean())
        if i >= 130 and i % rebal == 0:
            top = score.iloc[i].dropna().nlargest(TOPN).index.tolist()
            if set(top) != set(held):
                e *= (1 - COST * len(set(top) ^ set(held)) / max(len(top), 1))
                held = top
        eq.append(e)
    return pd.Series(eq, index=dates)


def stats(c):
    r = c.pct_change().dropna()
    y = (c.index[-1] - c.index[0]).days / 365.25
    return (c.iloc[-1] - 1, c.iloc[-1] ** (1 / y) - 1, (c / c.cummax() - 1).min(),
            r.mean() / r.std() * np.sqrt(252) if r.std() else 0)


def main():
    px, spy = load_sectors()
    print(f"섹터 ETF {px.shape[1]}종 · {px.index[0].date()}~{px.index[-1].date()} "
          f"({(px.index[-1]-px.index[0]).days/365.25:.1f}년)")
    if (px.index[-1] - px.index[0]).days < 365 * 3:
        print("⚠ 기간이 3년 미만 — 결론 신뢰도 낮음")
    scores = build_scores(px, spy)
    spy_c = spy / spy.iloc[0]
    ew = px.pct_change().mean(axis=1).fillna(0).add(1).cumprod()

    print(f"\n=== 섹터 top{TOPN} 월1회 로테이션 (편도 5bp) ===")
    print(f"{'전략':30}{'총수익':>9}{'CAGR':>8}{'MDD':>8}{'샤프':>7}")
    for lab, c in (("SPY 단순보유", spy_c), ("섹터 균등보유(EW)", ew)):
        t_, cg, dd, sh = stats(c)
        print(f"{lab:30}{t_:>+9.0%}{cg:>+8.1%}{dd:>+8.1%}{sh:>7.2f}")
    print("-" * 62)
    for name, sc in scores.items():
        t_, cg, dd, sh = stats(run(px, sc))
        print(f"{name:30}{t_:>+9.0%}{cg:>+8.1%}{dd:>+8.1%}{sh:>7.2f}")
    print("\n※ 판정: 다차원(④~⑥)이 단순 모멘텀(①)을 못 이기면 '복잡성 무익' 재확인")


if __name__ == "__main__":
    main()
