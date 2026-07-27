"""팩터 리서치 프레임 — IC/IR·분위 스프레드로 팩터 예측력을 객관 측정.

지금까지 팩터를 '백테스트 총수익'으로 판단했는데, 그건 슬롯·비용·경로에 의존한다.
IC(정보계수)는 **팩터값과 미래수익의 순위상관** — 전략 구현과 무관한 팩터 자체의 힘이다.

- IC(t) = spearman( factor(t), forward_return(t→t+H) )  (전 종목 횡단면)
- IC 평균 > 0.02~0.03 이면 실전 가치 있음(업계 통념), IR = mean(IC)/std(IC) > 0.3 이면 안정적
- 분위 스프레드 = 상위20% 평균수익 - 하위20% 평균수익 (단조성 확인)
- t-stat = IR × sqrt(N) — 우연이 아닐 확률

평가 팩터: rs21·rs63·복합점수·high_prox·vol_surge·rsi2역·퀄리티(정적)
실행: python scripts/factor_research.py
"""
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bt_cache import load_cache  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
QCACHE = ROOT / "data" / "us_quality.pkl"
HORIZONS = (5, 21, 63)          # 1주 / 1개월 / 3개월 forward
STEP = 5                        # IC 샘플 간격(거래일) — 중복 표본 완화


def _rsi(df, n=2):
    d = df.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def build_factors(px, spy):
    """평가 대상 팩터들 (모두 '클수록 좋다' 방향으로 정렬)."""
    ret21, ret63 = px / px.shift(21) - 1, px / px.shift(63) - 1
    s21, s63 = spy / spy.shift(21) - 1, spy / spy.shift(63) - 1
    rs21 = ret21.sub(s21.reindex(px.index), axis=0)
    rs63 = ret63.sub(s63.reindex(px.index), axis=0)
    high_prox = px / px.rolling(252, min_periods=120).max()
    pr = lambda d: d.rank(axis=1, pct=True)                              # noqa: E731
    comp = 0.30 * pr(rs63) + 0.25 * pr(rs21) + 0.25 * pr(ret63) + 0.20 * pr(high_prox)
    vol = px.pct_change().rolling(21).std()
    f = {
        "rs21 (1개월 모멘텀)": rs21,
        "rs63 (3개월 모멘텀)": rs63,
        "복합점수": comp,
        "high_prox (52주고점근접)": high_prox,
        "저변동성 (-21일 변동성)": -vol,
        "역RSI2 (과매도=높은점수)": 100 - _rsi(px, 2),
    }
    if QCACHE.exists():                                                  # 정적 퀄리티 (look-ahead 유의)
        q = pickle.loads(QCACHE.read_bytes())
        s = pd.DataFrame(index=q.index)
        for col, sign in (("roe", 1), ("fcf_yield", 1), ("op_margin", 1), ("debt_eq", -1)):
            v = pd.to_numeric(q[col], errors="coerce")
            s[col] = v.rank(pct=True) if sign > 0 else 1 - v.rank(pct=True)
        qs = s.mean(axis=1, skipna=True).reindex(px.columns)
        f["퀄리티 (정적·look-ahead)"] = pd.DataFrame(
            np.tile(qs.values, (len(px), 1)), index=px.index, columns=px.columns)
    return f


def _spearman(a, b):
    """순위 피어슨 = 스피어만 (scipy 의존 없이)."""
    return a.rank().corr(b.rank())


def ic_series(fac, fwd):
    """행별 스피어만 상관 (팩터 vs forward수익)."""
    out = []
    for i in range(260, len(fac) - 1, STEP):
        a, b = fac.iloc[i], fwd.iloc[i]
        m = a.notna() & b.notna()
        if m.sum() >= 50:
            out.append((fac.index[i], _spearman(a[m], b[m])))
    if not out:
        return pd.Series(dtype=float)
    return pd.Series([v for _, v in out], index=[d for d, _ in out]).dropna()


def quantile_spread(fac, fwd, q=5):
    """상위분위 - 하위분위 평균 forward수익 + 단조성(분위별 평균)."""
    rows = []
    for i in range(260, len(fac) - 1, STEP):
        a, b = fac.iloc[i], fwd.iloc[i]
        m = a.notna() & b.notna()
        if m.sum() < 50:
            continue
        lab = pd.qcut(a[m].rank(method="first"), q, labels=False)
        rows.append(b[m].groupby(lab).mean())
    if not rows:
        return None, None
    qm = pd.DataFrame(rows).mean()
    return qm.iloc[-1] - qm.iloc[0], qm


def main():
    px, spy = load_cache()
    px = px.loc[:, px.notna().sum() >= 300]
    print(f"유니버스 {px.shape[1]}종목 · {px.index[0].date()}~{px.index[-1].date()}")
    factors = build_factors(px, spy)

    for H in HORIZONS:
        fwd = px.shift(-H) / px - 1
        print(f"\n{'='*78}\nforward {H}일 수익 기준\n{'='*78}")
        print(f"{'팩터':26}{'IC평균':>9}{'IC표준':>8}{'IR':>7}{'t-stat':>8}"
              f"{'IC>0비율':>9}{'분위스프레드':>12}{'판정':>8}")
        for name, fac in factors.items():
            ic = ic_series(fac, fwd)
            if len(ic) < 20:
                print(f"{name:26}  (표본부족)")
                continue
            m, s = ic.mean(), ic.std()
            ir = m / s if s else 0
            t = ir * np.sqrt(len(ic))
            sp, _ = quantile_spread(fac, fwd)
            # 업계 표준(Vibe-Trading factor-research 스킬 기준):
            #   IC>0.03 기본 예측력 / >0.05 강함 / >0.10 look-ahead 의심
            #   IR>0.5 안정적 / IC>0비율 55%+ 방향 안정 (50% 미만은 사용 불가)
            #   ※ 음수 IC도 역방향 팩터로 유효 — 절댓값으로 판정
            pos = (ic > 0).mean()
            stable = pos >= 0.55 or pos <= 0.45          # 방향 일관성(역방향 포함)
            verdict = ("의심" if abs(m) >= 0.10 else
                       "강함" if abs(m) >= 0.05 and abs(ir) >= 0.5 and stable else
                       "유효" if abs(m) >= 0.03 and stable else
                       "약함" if abs(t) >= 2 else "무의미")
            print(f"{name:26}{m:>+9.4f}{s:>8.3f}{ir:>+7.2f}{t:>+8.1f}"
                  f"{pos:>9.0%}{(sp or 0) * 100:>+11.2f}%{verdict:>8}")

    # 최고 팩터의 분위 단조성 (63일)
    print(f"\n{'='*78}\n분위별 평균 forward 63일 수익 (단조 증가면 팩터가 건강)\n{'='*78}")
    fwd63 = px.shift(-63) / px - 1
    for name in ("rs63 (3개월 모멘텀)", "복합점수", "역RSI2 (과매도=높은점수)"):
        if name not in factors:
            continue
        _, qm = quantile_spread(factors[name], fwd63)
        if qm is not None:
            print(f"  {name:26}" + "  ".join(f"Q{i+1} {v*100:+5.1f}%" for i, v in enumerate(qm)))
    print("\n판정(업계 표준): |IC|≥0.05 & |IR|≥0.5 & 방향안정 = 강함 / |IC|≥0.03 = 유효")
    print("                |IC|≥0.10 = look-ahead 의심 / IC>0비율 45~55% = 방향 불안정")
    print("※ 음수 IC도 역방향 팩터로 유효 — 절댓값 판정")
    print("※ 정적 퀄리티는 look-ahead 편향 있어 IC가 과대 — 참고용")
    print("⚠ IC는 단조 관계만 측정 — 모멘텀처럼 U자(양극단 우수) 팩터는 IC가 0에 가깝게 나온다.")
    print("  반드시 위 '분위별 수익' 단조성과 함께 판단할 것 (2026-07-27 실제 함정)")


if __name__ == "__main__":
    main()
