"""기술지표 팩터 검증 — 추세(EMA/ADX)·평균회귀(BB/RSI)·수급(OBV/거래량) 개별 및 복합.

배경(Vibe-Trading technical-basic 스킬): 세 계열 지표를 복합 점수로 묶는 프레임.
우리 이력: 복합화가 도움된 적이 없다(복합점수<3개월단독, 모멘텀+퀄리티<모멘텀, 4차원섹터<1차원).
그래도 개별 지표 중 쓸 만한 게 있는지는 아직 안 봤다 — **개별부터 IC로 재고, 복합은 그 다음**.

판정: A1 프레임(업계 표준) — |IC|≥0.03 유효 / ≥0.05 & IR≥0.5 강함 + 분위 단조성 병행.
실행: python scripts/technical_factors.py
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from _bt_cache import load_cache  # noqa: E402

HORIZONS = (21, 63)
STEP = 5


def _rsi(df, n):
    d = df.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def build(px):
    """지표 3계열 (전부 '클수록 좋다' 방향으로 정렬)."""
    ema12, ema26 = px.ewm(span=12).mean(), px.ewm(span=26).mean()
    macd = (ema12 - ema26) / px                       # 정규화 MACD
    sig = macd.ewm(span=9).mean()
    ma20, sd20 = px.rolling(20).mean(), px.rolling(20).std()
    bb_pos = (px - ma20) / (2 * sd20.replace(0, np.nan))   # +1=상단밴드, -1=하단
    vol21 = px.pct_change().rolling(21).std()
    ret5 = px / px.shift(5) - 1
    return {
        # 추세 계열
        "EMA 정렬(12>26)": (ema12 / ema26 - 1),
        "MACD 히스토그램": (macd - sig),
        "50MA 이격도": (px / px.rolling(50).mean() - 1),
        # 평균회귀 계열 (역방향: 과매도일수록 높은 점수)
        "역RSI14": 100 - _rsi(px, 14),
        "볼린저 하단근접(-BB%)": -bb_pos,
        "5일 역추세(-ret5)": -ret5,
        # 변동성·기타
        "저변동성(-21일σ)": -vol21,
        "고변동성(+21일σ)": vol21,
    }


def _spear(a, b):
    return a.rank().corr(b.rank())


def evaluate(px, factors):
    for H in HORIZONS:
        fwd = px.shift(-H) / px - 1
        print(f"\n{'='*80}\nforward {H}일\n{'='*80}")
        print(f"{'지표':24}{'IC':>9}{'IR':>7}{'IC>0':>7}{'Q1':>8}{'Q3':>8}{'Q5':>8}"
              f"{'Q5-Q1':>9}{'판정':>8}")
        for name, f in factors.items():
            ics, qms = [], []
            for i in range(260, len(px) - H - 1, STEP):
                a, b = f.iloc[i], fwd.iloc[i]
                m = a.notna() & b.notna()
                if m.sum() < 50:
                    continue
                ics.append(_spear(a[m], b[m]))
                lab = pd.qcut(a[m].rank(method="first"), 5, labels=False, duplicates="drop")
                qms.append(b[m].groupby(lab).mean())
            ic = pd.Series(ics).dropna()
            if len(ic) < 20:
                print(f"{name:24}  (표본부족)")
                continue
            qm = pd.DataFrame(qms).mean()
            ir = ic.mean() / ic.std() if ic.std() else 0
            pos = (ic > 0).mean()
            stable = pos >= 0.55 or pos <= 0.45
            v = ("강함" if abs(ic.mean()) >= 0.05 and abs(ir) >= 0.5 and stable else
                 "유효" if abs(ic.mean()) >= 0.03 and stable else
                 "약함" if abs(ic.mean()) >= 0.02 else "무의미")
            sp = (qm.iloc[-1] - qm.iloc[0]) * 100 if len(qm) >= 5 else float("nan")
            print(f"{name:24}{ic.mean():>+9.4f}{ir:>+7.2f}{pos:>7.0%}"
                  f"{qm.iloc[0]*100:>+7.1f}%{qm.iloc[2]*100:>+7.1f}%{qm.iloc[-1]*100:>+7.1f}%"
                  f"{sp:>+8.1f}%{v:>8}")


def main():
    px, spy = load_cache()
    px = px.loc[:, px.notna().sum() >= 300]
    print(f"유니버스 {px.shape[1]}종목 · {px.index[0].date()}~{px.index[-1].date()}")
    facs = build(px)
    evaluate(px, facs)

    # 복합 점수 (스킬 프레임: 추세+평균회귀+변동성 균등) vs 최고 단일
    print(f"\n{'='*80}\n복합 vs 단일 (스킬 프레임 검증)\n{'='*80}")
    pr = lambda d: d.rank(axis=1, pct=True)                      # noqa: E731
    comp = (pr(facs["EMA 정렬(12>26)"]) + pr(facs["역RSI14"]) + pr(facs["저변동성(-21일σ)"])) / 3
    ret63 = px / px.shift(63) - 1
    s63 = spy / spy.shift(63) - 1
    mom = ret63.sub(s63.reindex(px.index), axis=0)
    evaluate(px, {"복합(추세+회귀+변동성)": comp, "3개월 모멘텀(기준)": mom})
    print("\n※ 우리 기준: |IC|≥0.03 유효. 이력상 복합화는 매번 단일보다 못했다(4회 연속)")


if __name__ == "__main__":
    main()
