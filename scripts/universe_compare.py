"""주도주 선정 기준별 유니버스 비교 — 1개월RS / 3개월RS / 복합점수.

질문: 대시보드 정렬 기준(순수1개월·순수3개월·복합점수)마다 뽑히는 종목이 다르다.
      각 기준으로 top-K를 골라 주기적 리밸런스 균등보유했을 때 성과가 어떻게 다른가?

- rs21  = 21일 시장(SPY)대비 초과수익  (순수 1개월 주도)
- rs63  = 63일 시장대비 초과수익        (순수 3개월 주도)
- comp  = 복합점수 근사(대시보드 공식): pct_rank 가중합
          0.30·rs63 + 0.25·rs21 + 0.25·abs63(절대수익) + 0.20·high_prox(52주고점근접)
          (섹터RS·거래량 성분은 캐시에 없어 제외 — 4성분 근사)
- 각 기준 top-K(기본 20)를 리밸런스 주기마다 균등보유, 편도 5bp. 룩어헤드 없음.
- 벤치마크: SPY 단순보유. 추가: 오늘 기준 세 유니버스 종목 겹침도.

실행: python scripts/universe_compare.py
"""
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
CACHE = Path(__file__).resolve().parents[1] / "data" / "us_px_cache.pkl"
COST = 0.0005                       # 편도 5bp
TOPK = 20


def _pct(df):                       # 행별(날짜별) 백분위 0~1
    return df.rank(axis=1, pct=True)


def load():
    px, spy = pickle.loads(CACHE.read_bytes())
    px = px.loc[:, px.notna().sum() >= 300]
    return px, spy


def signals(px, spy):
    ret21 = px / px.shift(21) - 1
    ret63 = px / px.shift(63) - 1
    s21 = spy / spy.shift(21) - 1
    s63 = spy / spy.shift(63) - 1
    rs21 = ret21.sub(s21.reindex(px.index), axis=0)
    rs63 = ret63.sub(s63.reindex(px.index), axis=0)
    high_prox = px / px.rolling(252, min_periods=120).max()      # 52주 고점比 (1.0=신고가)
    comp = (0.30 * _pct(rs63) + 0.25 * _pct(rs21) + 0.25 * _pct(ret63)
            + 0.20 * _pct(high_prox))
    # 추세 필터(공통): 종가 > 50MA — 하락종목 배제
    trend = px > px.rolling(50).mean()
    return {"rs21": rs21, "rs63": rs63, "comp": comp}, trend


def run(px, score, trend, rebal=5):
    """score 상위 TOPK 균등보유, rebal일마다 재구성. 자본곡선 반환."""
    dates = px.index
    daily = px.pct_change().fillna(0)
    equity, eq = 1.0, []
    held, warm = [], 260
    turn = 0
    for i, d in enumerate(dates):
        if i > 0 and held:                          # 전일 보유분 오늘 수익
            equity *= (1 + daily.iloc[i][held].mean())
        if i >= warm and i % rebal == 0:            # 리밸런스일
            sc = score.iloc[i].where(trend.iloc[i])
            top = sc.dropna().nlargest(TOPK).index.tolist()
            if set(top) != set(held):
                changed = len(set(top) ^ set(held)) / max(len(top), 1)
                equity *= (1 - COST * changed)      # 교체분만 비용
                turn += changed
                held = top
        eq.append(equity)
    return pd.Series(eq, index=dates), turn


def stats(c, spy):
    r = c.pct_change().dropna()
    yrs = (c.index[-1] - c.index[0]).days / 365.25
    tot = c.iloc[-1] - 1
    cagr = c.iloc[-1] ** (1 / yrs) - 1
    dd = (c / c.cummax() - 1).min()
    sharpe = r.mean() / r.std() * np.sqrt(252) if r.std() else 0
    return tot, cagr, dd, sharpe


def main():
    px, spy = load()
    print(f"유니버스 {px.shape[1]}종목 · {px.index[0].date()}~{px.index[-1].date()}")
    score, trend = signals(px, spy)
    spy_c = spy.reindex(px.index).ffill()
    spy_c = spy_c / spy_c.iloc[0]
    bt, bc, bd, bs = stats(spy_c, spy)
    print(f"\nSPY 단순보유:           총 {bt:>+7.0%}  CAGR {bc:>+6.1%}  MDD {bd:>+6.1%}  샤프 {bs:.2f}")

    LAB = {"rs21": "순수 1개월 주도", "rs63": "순수 3개월 주도", "comp": "복합점수"}
    for rebal, tag in ((5, "주1회"), (21, "월1회")):
        print(f"\n=== 리밸런스 {tag} · top{TOPK} 균등보유 (편도 5bp) ===")
        print(f"{'기준':16}{'총수익':>9}{'CAGR':>8}{'MDD':>8}{'샤프':>7}{'회전':>7}")
        for key in ("rs21", "rs63", "comp"):
            c, turn = run(px, score[key], trend, rebal)
            tot, cagr, dd, sh = stats(c, spy)
            print(f"{LAB[key]:16}{tot:>+9.0%}{cagr:>+8.1%}{dd:>+8.1%}{sh:>7.2f}"
                  f"{turn / ((len(px) - 260) / rebal):>6.0%}")

    # 오늘 기준 세 유니버스 종목 구성 차이
    print("\n=== 최신일 top20 종목 겹침도 ===")
    last = px.index[-1]
    sets = {}
    for key in ("rs21", "rs63", "comp"):
        sc = score[key].iloc[-1].where(trend.iloc[-1])
        sets[key] = set(sc.dropna().nlargest(TOPK).index)
    for a, b in (("rs21", "rs63"), ("rs21", "comp"), ("rs63", "comp")):
        ov = len(sets[a] & sets[b])
        print(f"  {LAB[a]} ∩ {LAB[b]}: {ov}/{TOPK} 겹침 ({ov / TOPK:.0%})")
    only21 = sets["rs21"] - sets["rs63"] - sets["comp"]
    print(f"  1개월 전용 종목(다른 기준엔 없음): {sorted(only21)[:8]}")
    common = sets["rs21"] & sets["rs63"] & sets["comp"]
    print(f"  세 기준 공통 종목: {sorted(common)[:8]}")


if __name__ == "__main__":
    main()
