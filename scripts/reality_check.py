"""'이 수익이 정말인가?' 현실성 검증 — 생존편향·비용·레짐 3중 스트레스.

배경: 3개월 모멘텀 백테스트가 +1769% 등으로 잘 나오는데, 그렇다면 왜 다들 부자가 아닌가?
      백테스트 수치가 부풀려지는 3대 원인을 데이터로 벗겨낸다.

1) 생존편향: 유니버스가 '현 S&P500 구성'이라 상폐 패자 부재 + 미래 승자 미리 알기.
   → 벤치마크를 SPY가 아니라 '같은 496종목 동일가중(EW)'으로. EW도 같은 편향을 가지므로,
   모멘텀이 EW를 이기는 만큼만이 '진짜 선택 알파'(편향 제거분).
2) 거래비용: 5bp는 낙관적. 5/20/50bp에서 모멘텀 총수익이 얼마나 무너지나.
3) 레짐 의존: 2015~26은 역사적 강세장. 연도별로 쪼개 하락장(2018Q4·2022)에서 붕괴하는가.
   특히 모멘텀 크래시(급반등장에서 모멘텀이 역행) 확인.

실행: python scripts/reality_check.py
"""
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
CACHE = Path(__file__).resolve().parents[1] / "data" / "us_px_cache.pkl"
TOPK = 20


def load():
    px, spy = pickle.loads(CACHE.read_bytes())
    return px.loc[:, px.notna().sum() >= 300], spy


def momentum_score(px, spy):
    ret63 = px / px.shift(63) - 1
    s63 = spy / spy.shift(63) - 1
    rs63 = ret63.sub(s63.reindex(px.index), axis=0)
    trend = px > px.rolling(50).mean()
    return rs63, trend


def run(px, score, trend, rebal, cost, equal_weight=False):
    """score 상위 TOPK 보유(equal_weight=True면 전종목 동일가중=EW 벤치). 자본곡선 반환."""
    dates = px.index
    daily = px.pct_change().fillna(0)
    equity, eq, held, warm = 1.0, [], [], 260
    for i in range(len(dates)):
        if i > 0 and held:
            equity *= (1 + daily.iloc[i][held].mean())
        if i >= warm and i % rebal == 0:
            if equal_weight:
                top = daily.iloc[i].index[px.iloc[i].notna()].tolist()
            else:
                sc = score.iloc[i].where(trend.iloc[i])
                top = sc.dropna().nlargest(TOPK).index.tolist()
            if set(top) != set(held):
                changed = len(set(top) ^ set(held)) / max(len(top), 1)
                equity *= (1 - cost * changed)
                held = top
        eq.append(equity)
    return pd.Series(eq, index=dates)


def cagr_mdd(c):
    yrs = (c.index[-1] - c.index[0]).days / 365.25
    return (c.iloc[-1] ** (1 / yrs) - 1, (c / c.cummax() - 1).min(),
            c.pct_change().dropna().pipe(lambda r: r.mean() / r.std() * np.sqrt(252)))


def main():
    px, spy = load()
    rs63, trend = momentum_score(px, spy)
    spy_c = (spy.reindex(px.index).ffill()).pipe(lambda s: s / s.iloc[0])

    print("=" * 70)
    print("검증 1: 생존편향 벗기기 — 모멘텀 vs 동일가중(EW, 같은 편향) vs SPY")
    print("=" * 70)
    mom = run(px, rs63, trend, 21, 0.0005)
    ew = run(px, None, None, 21, 0.0005, equal_weight=True)
    for name, c in (("3개월 모멘텀 top20", mom), ("동일가중 496종목(EW)", ew), ("SPY 단순보유", spy_c)):
        cg, dd, sh = cagr_mdd(c)
        print(f"  {name:22} 총 {c.iloc[-1] - 1:>+8.0%}  CAGR {cg:>+6.1%}  MDD {dd:>+6.1%}  샤프 {sh:.2f}")
    m_cg = cagr_mdd(mom)[0]
    e_cg = cagr_mdd(ew)[0]
    print(f"\n  → 모멘텀의 '진짜 선택 알파'(EW 대비 초과 CAGR): {(m_cg - e_cg) * 100:+.1f}%p/년")
    print(f"    (EW가 이미 SPY를 {(e_cg - cagr_mdd(spy_c)[0]) * 100:+.1f}%p 이기는 건 순전히 생존편향)")

    print("\n" + "=" * 70)
    print("검증 2: 거래비용 민감도 — 모멘텀 top20, 월1회")
    print("=" * 70)
    for bp in (5, 20, 50, 100):
        c = run(px, rs63, trend, 21, bp / 10000)
        cg, dd, sh = cagr_mdd(c)
        print(f"  편도 {bp:>3}bp: 총 {c.iloc[-1] - 1:>+8.0%}  CAGR {cg:>+6.1%}  샤프 {sh:.2f}")

    print("\n" + "=" * 70)
    print("검증 3: 레짐 의존 — 연도별 수익 (모멘텀 vs EW vs SPY)")
    print("=" * 70)
    print(f"  {'연도':6}{'모멘텀':>10}{'EW':>10}{'SPY':>10}{'모멘텀-EW':>11}")
    for yr in range(2016, 2027):
        sl = slice(f"{yr}-01-01", f"{yr}-12-31")
        def yret(c):
            w = c[sl]
            return (w.iloc[-1] / w.iloc[0] - 1) if len(w) > 1 else np.nan
        m, e, s = yret(mom), yret(ew), yret(spy_c)
        flag = "  ⚠약세" if s < 0 else ""
        print(f"  {yr:6}{m:>+10.0%}{e:>+10.0%}{s:>+10.0%}{(m - e):>+11.0%}{flag}")

    print("\n" + "=" * 70)
    print("검증 4: 주도주 전환 속도 — 재평가 주기별 (모멘텀, 20bp 현실비용)")
    print("=" * 70)
    for rebal, tag in ((5, "주1회"), (10, "2주1회"), (21, "월1회"), (63, "분기1회")):
        c = run(px, rs63, trend, rebal, 0.0020)
        cg, dd, sh = cagr_mdd(c)
        print(f"  {tag:8} 재평가: 총 {c.iloc[-1] - 1:>+8.0%}  CAGR {cg:>+6.1%}  "
              f"MDD {dd:>+6.1%}  샤프 {sh:.2f}")


if __name__ == "__main__":
    main()
