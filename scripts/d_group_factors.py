"""D그룹 통합 검증 — ETF 자금흐름 프록시 / 배당 / 계절성.

세 스킬(us-etf-flow·dividend-analysis·seasonal)이 각각 작아 한 번에 검증한다.

D1 자금흐름: 무료로 ETF 순유입 데이터가 없으므로 **거래대금 급증**을 프록시로 사용
    (스킬 원안은 creation/redemption 데이터 — 유료). 섹터 ETF 거래대금 모멘텀.
D2 배당: 배당수익률 팩터 (yfinance dividendYield). 고배당이 우수한가?
D3 계절성: 월별 효과("5월에 팔아라"), 요일 효과, 월말/월초 효과.

판정: A1 업계표준(|IC|≥0.03) + 분위 단조성. 계절성은 월별 평균수익 유의성으로.
실행: python scripts/d_group_factors.py
"""
import pickle
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from _bt_cache import load_cache  # noqa: E402

DCACHE = ROOT / "data" / "us_dividend.pkl"


def _spear(a, b):
    return a.rank().corr(b.rank())


def ic_of(px, fac, H=63, step=5):
    """단일 팩터 IC·분위 (정적 시리즈면 DataFrame으로 확장)."""
    fwd = px.shift(-H) / px - 1
    if isinstance(fac, pd.Series):
        fac = pd.DataFrame(np.tile(fac.reindex(px.columns).values, (len(px), 1)),
                           index=px.index, columns=px.columns)
    ics, qms = [], []
    for i in range(260, len(px) - H - 1, step):
        a, b = fac.iloc[i], fwd.iloc[i]
        m = a.notna() & b.notna()
        if m.sum() < 50:
            continue
        ics.append(_spear(a[m], b[m]))
        lab = pd.qcut(a[m].rank(method="first"), 5, labels=False, duplicates="drop")
        qms.append(b[m].groupby(lab).mean())
    ic = pd.Series(ics).dropna()
    if len(ic) < 20:
        return None
    qm = pd.DataFrame(qms).mean()
    ir = ic.mean() / ic.std() if ic.std() else 0
    pos = (ic > 0).mean()
    stable = pos >= 0.55 or pos <= 0.45
    v = ("강함" if abs(ic.mean()) >= 0.05 and abs(ir) >= 0.5 and stable else
         "유효" if abs(ic.mean()) >= 0.03 and stable else
         "약함" if abs(ic.mean()) >= 0.02 else "무의미")
    return {"ic": ic.mean(), "ir": ir, "pos": pos, "q": qm, "v": v}


def d1_flow(px, spy):
    """D1: 거래대금 급증(자금유입 프록시) — 캐시엔 종가만 있어 DB 거래량 사용."""
    from src import db

    con = db.connect()
    rows = con.execute("SELECT symbol, date, volume, close FROM prices_daily "
                       "WHERE market='US_STOCK' AND volume > 0 ORDER BY date").fetchall()
    con.close()
    df = pd.DataFrame([dict(r) for r in rows])
    if df.empty:
        print("  (거래량 데이터 없음)")
        return
    df["val"] = df["volume"] * df["close"]
    vp = df.pivot(index="date", columns="symbol", values="val")
    vp.index = pd.to_datetime(vp.index)
    surge = vp / vp.rolling(60).mean()                 # 60일 평균 대비 거래대금
    common = px.columns.intersection(surge.columns)
    px2 = px.loc[px.index.intersection(surge.index), common]
    surge = surge.loc[px2.index, common]
    if len(px2) < 300:
        print(f"  (겹치는 기간 부족: {len(px2)}일)")
        return
    r = ic_of(px2, surge)
    if r:
        print(f"  거래대금 급증(60일평균比): IC {r['ic']:+.4f} · IR {r['ir']:+.2f} · "
              f"Q1 {r['q'].iloc[0]*100:+.1f}% → Q5 {r['q'].iloc[-1]*100:+.1f}% · {r['v']}")


def d2_dividend(px, symbols):
    """D2: 배당수익률 팩터."""
    if DCACHE.exists():
        dv = pickle.loads(DCACHE.read_bytes())
    else:
        import yfinance as yf

        rows = {}
        for i, s in enumerate(symbols):
            try:
                info = yf.Ticker(s).info
                rows[s] = {"div_yield": info.get("dividendYield"),
                           "payout": info.get("payoutRatio")}
            except Exception:
                pass
            if i % 50 == 0:
                print(f"  배당 수집 {i}/{len(symbols)}...", flush=True)
                time.sleep(0.4)
        dv = pd.DataFrame(rows).T
        DCACHE.write_bytes(pickle.dumps(dv))
    y = pd.to_numeric(dv["div_yield"], errors="coerce").dropna()
    print(f"  배당 확보 {len(y)}종목 (무배당 제외) · 중앙값 {y.median():.2f}%")
    r = ic_of(px, y)
    if r:
        print(f"  배당수익률: IC {r['ic']:+.4f} · IR {r['ir']:+.2f} · "
              f"Q1 {r['q'].iloc[0]*100:+.1f}% → Q5 {r['q'].iloc[-1]*100:+.1f}% · {r['v']}")


def d3_seasonal(px, spy):
    """D3: 계절성 — 월별·요일·월말 효과 (SPY 기준, 통계적 유의성 포함)."""
    s = spy.reindex(px.index).ffill()
    r = s.pct_change().dropna()
    print("\n  [월별 효과] 평균 일수익 × 21 (월 환산)")
    print(f"  {'월':>4}{'평균':>9}{'승률':>7}{'N':>6}{'t값':>7}")
    for m in range(1, 13):
        g = r[r.index.month == m]
        if len(g) < 40:
            continue
        t = g.mean() / (g.std() / np.sqrt(len(g))) if g.std() else 0
        print(f"  {m:>3}월{g.mean()*21*100:>+8.2f}%{(g > 0).mean():>7.0%}{len(g):>6}{t:>+7.2f}")
    print("\n  [요일 효과]")
    for i, nm in enumerate(["월", "화", "수", "목", "금"]):
        g = r[r.index.dayofweek == i]
        if len(g) < 40:
            continue
        t = g.mean() / (g.std() / np.sqrt(len(g))) if g.std() else 0
        print(f"  {nm}요일 {g.mean()*100:>+6.3f}%/일 · 승률 {(g > 0).mean():.0%} · t {t:+.2f}")
    # 월말·월초 효과 (마지막 3일 + 첫 3일)
    dom = r.index.day
    eom = r[(dom >= 28) | (dom <= 3)]
    rest = r[(dom < 28) & (dom > 3)]
    print(f"\n  [월말월초 효과] 월말3일+월초3일 {eom.mean()*100:+.3f}%/일 vs "
          f"나머지 {rest.mean()*100:+.3f}%/일 (차이 {(eom.mean()-rest.mean())*100:+.3f}%p)")
    print("\n  ※ |t|>2 여야 통계적 유의. 계절성은 표본이 겹쳐 과대해석 주의")


def main():
    px, spy = load_cache()
    px = px.loc[:, px.notna().sum() >= 300]
    print(f"유니버스 {px.shape[1]}종목 · {px.index[0].date()}~{px.index[-1].date()}")
    print(f"\n{'='*76}\nD1. 자금흐름 프록시 (거래대금 급증)\n{'='*76}")
    d1_flow(px, spy)
    print(f"\n{'='*76}\nD2. 배당수익률\n{'='*76}")
    d2_dividend(px, list(px.columns))
    print(f"\n{'='*76}\nD3. 계절성\n{'='*76}")
    d3_seasonal(px, spy)


if __name__ == "__main__":
    main()
