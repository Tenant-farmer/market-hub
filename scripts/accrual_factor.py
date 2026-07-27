"""발생액(이익의 질) 팩터 검증 — '장부 이익 vs 실제 현금'.

배경(Vibe-Trading financial-statement 스킬):
    accrual_ratio = (순이익 - 영업현금흐름) / 총자산
    > 10% 이면 이익이 현금이 아닌 회계 조정으로 부풀려진 것 → 이익의 질 나쁨

학술적으로 'accrual anomaly'(Sloan 1996)는 검증된 팩터다: 발생액이 낮은(현금이 실한) 기업이
높은 기업보다 이후 수익률이 높다. **낮을수록 좋으므로 -accrual을 팩터값으로 쓴다.**

우리 퀄리티 검증(ROE·FCF·부채·마진)에는 이 축이 없었다 — 별도 검증 가치가 있다.
데이터: yfinance 분기 재무제표(순이익·영업현금흐름·총자산). 현재 스냅샷이라 look-ahead 유의.

실행: python scripts/accrual_factor.py
"""
import pickle
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bt_cache import load_cache  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
ACACHE = ROOT / "data" / "us_accrual.pkl"
TOPK, COST = 20, 0.0005


def load_accruals(symbols):
    """yfinance 재무제표 → 발생액 비율 (캐시)."""
    if ACACHE.exists():
        d = pickle.loads(ACACHE.read_bytes())
        if len(d) >= len(symbols) * 0.5:
            return d
    import yfinance as yf

    rows = {}
    for i, sym in enumerate(symbols):
        try:
            t = yf.Ticker(sym)
            fin = t.quarterly_financials
            cf = t.quarterly_cashflow
            bs = t.quarterly_balance_sheet
            if fin is None or cf is None or bs is None or fin.empty or cf.empty or bs.empty:
                continue

            def _pick(df, keys):
                for k in keys:
                    if k in df.index:
                        v = df.loc[k].dropna()
                        if len(v):
                            return float(v.iloc[0])
                return None
            ni = _pick(fin, ["Net Income", "Net Income Common Stockholders"])
            cfo = _pick(cf, ["Operating Cash Flow", "Total Cash From Operating Activities"])
            ta = _pick(bs, ["Total Assets"])
            if ni is None or cfo is None or not ta:
                continue
            rows[sym] = {"ni": ni, "cfo": cfo, "ta": ta, "accrual": (ni - cfo) / ta}
        except Exception:
            pass
        if i % 50 == 0:
            print(f"  재무 수집 {i}/{len(symbols)}...", flush=True)
            time.sleep(0.4)
    d = pd.DataFrame(rows).T
    ACACHE.write_bytes(pickle.dumps(d))
    return d


def run(px, score, trend, mask=None, rebal=21):
    dates, daily = px.index, px.pct_change().fillna(0)
    eq, held, e = [], [], 1.0
    for i in range(len(dates)):
        if i > 0 and held:
            e *= (1 + daily.iloc[i][held].mean())
        if i >= 260 and i % rebal == 0:
            sc = score.iloc[i].where(trend.iloc[i])
            if mask is not None:
                sc = sc[sc.index.isin(mask)]
            top = sc.dropna().nlargest(TOPK).index.tolist()
            if set(top) != set(held):
                e *= (1 - COST * len(set(top) ^ set(held)) / max(len(top), 1))
                held = top
        eq.append(e)
    return pd.Series(eq, index=dates)


def stats(c):
    r = c.pct_change().dropna()
    y = (c.index[-1] - c.index[0]).days / 365.25
    return (c.iloc[-1] - 1, c.iloc[-1] ** (1 / y) - 1, (c / c.cummax() - 1).min(),
            r.mean() / r.std() * np.sqrt(252))


def main():
    px, spy = load_cache()
    px = px.loc[:, px.notna().sum() >= 300]
    print(f"유니버스 {px.shape[1]}종목 · {px.index[0].date()}~{px.index[-1].date()}")
    acc = load_accruals(list(px.columns))
    if acc is None or len(acc) == 0:
        sys.exit("[중단] 재무 데이터 없음")
    a = pd.to_numeric(acc["accrual"], errors="coerce").dropna()
    print(f"발생액 확보 {len(a)}종목 · 중앙값 {a.median():.1%} · "
          f"10%초과(이익질 나쁨) {(a > 0.10).mean():.0%}")

    ret63 = px / px.shift(63) - 1
    s63 = spy / spy.shift(63) - 1
    mom = ret63.sub(s63.reindex(px.index), axis=0)
    trend = px > px.rolling(50).mean()
    spy_c = (spy.reindex(px.index).ffill()).pipe(lambda s: s / s.iloc[0])

    print(f"\n{'전략':30}{'총수익':>9}{'CAGR':>8}{'MDD':>8}{'샤프':>7}")
    for lab, c in (("SPY 단순보유", spy_c), ("① 모멘텀 단독", run(px, mom, trend))):
        t_, cg, dd, sh = stats(c)
        print(f"{lab:30}{t_:>+9.0%}{cg:>+8.1%}{dd:>+8.1%}{sh:>7.2f}")
    # 발생액 필터: 낮은(현금이 실한) 종목만
    for q, lab in ((0.5, "하위50%"), (0.7, "하위70%")):
        keep = a[a <= a.quantile(q)].index
        t_, cg, dd, sh = stats(run(px, mom, trend, mask=keep))
        print(f"{'② 모멘텀+발생액 ' + lab:30}{t_:>+9.0%}{cg:>+8.1%}{dd:>+8.1%}{sh:>7.2f}")
    # 스킬 기준선: accrual > 10% 제외
    keep10 = a[a <= 0.10].index
    t_, cg, dd, sh = stats(run(px, mom, trend, mask=keep10))
    print(f"{'③ 모멘텀+발생액≤10%(스킬기준)':30}{t_:>+9.0%}{cg:>+8.1%}{dd:>+8.1%}{sh:>7.2f}")
    # 발생액 단독 (역방향: 낮을수록 좋음)
    inv = pd.DataFrame(np.tile((-a).reindex(px.columns).values, (len(px), 1)),
                       index=px.index, columns=px.columns)
    t_, cg, dd, sh = stats(run(px, inv, trend))
    print(f"{'④ 저발생액 단독':30}{t_:>+9.0%}{cg:>+8.1%}{dd:>+8.1%}{sh:>7.2f}")

    # IC (팩터 자체 예측력)
    print("\n=== 발생액 팩터 IC (음수=낮은 발생액이 우수, 절댓값 판정) ===")
    for H in (21, 63):
        fwd = px.shift(-H) / px - 1
        ics = []
        for i in range(260, len(px) - H - 1, 5):
            b = fwd.iloc[i]
            m = b.notna() & b.index.isin(a.index)
            if m.sum() >= 50:
                ics.append(a.reindex(b[m].index).rank().corr(b[m].rank()))
        ics = pd.Series(ics).dropna()
        if len(ics) > 20:
            print(f"  forward {H}일: IC {ics.mean():+.4f} · IR {ics.mean()/ics.std():+.2f} · "
                  f"IC<0비율 {(ics < 0).mean():.0%}")
    print("\n※ 발생액은 현재 스냅샷 — look-ahead 편향 있어 방향 참고용")


if __name__ == "__main__":
    main()
