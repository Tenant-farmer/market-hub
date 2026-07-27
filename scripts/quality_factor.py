"""퀄리티 팩터 검증 — 모멘텀에 '진짜 돈 버는가' 필터를 씌우면 나아지나.

사용자 통찰: 테크주에 PBR·PER은 깨졌다 → 밸류 대신 **퀄리티**(FCF·ROE·부채)로 거품 필터.
가설: 모멘텀 top 종목 중 '실적 없이 테마로만 오른 종목'을 걸러내면 낙폭↓·샤프↑.

- 유니버스: 캐시 496종목, 3개월 모멘텀(rs63) 상위 + 50MA 추세 (기존 최강 전략)
- 퀄리티 데이터: yfinance info (ROE·FCF·부채비율·이익률) — 1회 수집 후 캐시
  ※ 현재 스냅샷이라 과거 시점 퀄리티가 아님(look-ahead 한계) → 결과는 '방향 참고'로만
- 비교: ①모멘텀 단독 ②모멘텀+퀄리티 상위50% ③모멘텀+퀄리티 상위70% ④퀄리티 단독
실행: python scripts/quality_factor.py
"""
import pickle
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bt_cache import load_cache  # noqa: E402  (짧은 캐시 가드)
ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "us_px_cache.pkl"
QCACHE = ROOT / "data" / "us_quality.pkl"
TOPK, COST = 20, 0.0005


def load_px():
    px, spy = load_cache()
    return px.loc[:, px.notna().sum() >= 300], spy


def load_quality(symbols):
    """yfinance에서 퀄리티 지표 수집 (캐시). ROE·FCF수익률·부채비율·영업이익률."""
    if QCACHE.exists():
        q = pickle.loads(QCACHE.read_bytes())
        if len(q) >= len(symbols) * 0.8:
            return q
    import yfinance as yf

    rows = {}
    for i, sym in enumerate(symbols):
        try:
            info = yf.Ticker(sym).info
            mcap = info.get("marketCap") or 0
            rows[sym] = {
                "roe": info.get("returnOnEquity"),
                "fcf_yield": (info.get("freeCashflow") or 0) / mcap if mcap else None,
                "debt_eq": info.get("debtToEquity"),
                "op_margin": info.get("operatingMargins"),
            }
        except Exception:
            pass
        if i % 50 == 0:
            print(f"  퀄리티 수집 {i}/{len(symbols)}...", flush=True)
            time.sleep(0.5)
    q = pd.DataFrame(rows).T
    QCACHE.write_bytes(pickle.dumps(q))
    return q


def quality_score(q):
    """퀄리티 종합점수 = ROE·FCF수익률·영업이익률 백분위 평균 - 부채비율 백분위."""
    s = pd.DataFrame(index=q.index)
    for col, sign in (("roe", 1), ("fcf_yield", 1), ("op_margin", 1), ("debt_eq", -1)):
        v = pd.to_numeric(q[col], errors="coerce")
        s[col] = v.rank(pct=True) if sign > 0 else (1 - v.rank(pct=True))
    return s.mean(axis=1, skipna=True)


def run(px, mom, trend, qmask=None, rebal=21):
    dates, daily = px.index, px.pct_change().fillna(0)
    equity, eq, held, warm = 1.0, [], [], 260
    for i in range(len(dates)):
        if i > 0 and held:
            equity *= (1 + daily.iloc[i][held].mean())
        if i >= warm and i % rebal == 0:
            sc = mom.iloc[i].where(trend.iloc[i])
            if qmask is not None:
                sc = sc[sc.index.isin(qmask)]
            top = sc.dropna().nlargest(TOPK).index.tolist()
            if set(top) != set(held):
                equity *= (1 - COST * len(set(top) ^ set(held)) / max(len(top), 1))
                held = top
        eq.append(equity)
    return pd.Series(eq, index=dates)


def stats(c):
    r = c.pct_change().dropna()
    yrs = (c.index[-1] - c.index[0]).days / 365.25
    return (c.iloc[-1] - 1, c.iloc[-1] ** (1 / yrs) - 1, (c / c.cummax() - 1).min(),
            r.mean() / r.std() * np.sqrt(252))


def main():
    px, spy = load_px()
    print(f"유니버스 {px.shape[1]}종목 · {px.index[0].date()}~{px.index[-1].date()}")
    q = load_quality(list(px.columns))
    qs = quality_score(q).dropna()
    print(f"퀄리티 점수 확보: {len(qs)}종목")

    ret63 = px / px.shift(63) - 1
    s63 = spy / spy.shift(63) - 1
    mom = ret63.sub(s63.reindex(px.index), axis=0)
    trend = px > px.rolling(50).mean()
    spy_c = (spy.reindex(px.index).ffill()).pipe(lambda s: s / s.iloc[0])

    print(f"\n{'전략':26}{'총수익':>9}{'CAGR':>8}{'MDD':>8}{'샤프':>7}")
    tot, cg, dd, sh = stats(spy_c)
    print(f"{'SPY 단순보유':26}{tot:>+9.0%}{cg:>+8.1%}{dd:>+8.1%}{sh:>7.2f}")
    tot, cg, dd, sh = stats(run(px, mom, trend))
    print(f"{'① 모멘텀 단독':26}{tot:>+9.0%}{cg:>+8.1%}{dd:>+8.1%}{sh:>7.2f}")
    for pct, lab in ((0.5, "상위50%"), (0.7, "상위70%")):
        keep = qs[qs >= qs.quantile(1 - pct)].index
        tot, cg, dd, sh = stats(run(px, mom, trend, qmask=keep))
        print(f"{'② 모멘텀+퀄리티 ' + lab:26}{tot:>+9.0%}{cg:>+8.1%}{dd:>+8.1%}{sh:>7.2f}")
    # 퀄리티 단독 (모멘텀 없이 — 퀄리티 자체 힘 확인)
    qonly = pd.DataFrame(np.tile(qs.reindex(px.columns).values, (len(px), 1)),
                         index=px.index, columns=px.columns)
    tot, cg, dd, sh = stats(run(px, qonly, trend))
    print(f"{'③ 퀄리티 단독':26}{tot:>+9.0%}{cg:>+8.1%}{dd:>+8.1%}{sh:>7.2f}")

    # 하락장(2022) 방어력 — 퀄리티 필터의 진짜 목적
    print("\n=== 하락장 방어력 (2022년) ===")
    for lab, c in (("모멘텀 단독", run(px, mom, trend)),
                   ("모멘텀+퀄리티50%", run(px, mom, trend,
                                        qmask=qs[qs >= qs.quantile(0.5)].index)),
                   ("SPY", spy_c)):
        w = c["2022-01-01":"2022-12-31"]
        print(f"  {lab:18}{w.iloc[-1] / w.iloc[0] - 1:>+8.1%}")
    print("\n※ 퀄리티는 현재 스냅샷(과거 시점값 아님) — look-ahead 있어 방향 참고용")


if __name__ == "__main__":
    main()
