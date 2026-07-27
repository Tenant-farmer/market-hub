"""밸류에이션 팩터 검증 — 전통 PER/PBR이 아닌 현대적 지표로.

사용자 통찰(2026-07-27): "테크주에 PBR·PER이 의미 있나?" → 맞다. PBR은 무형자산(R&D·브랜드)을
장부에 못 담아 테크에서 깨졌고, PER은 미래이익을 무시한다.
그래서 스킬(valuation-model)이 제시한 **상대가치 3종**과 현대적 대안을 함께 본다:
  - PB-ROE 매트릭스: 같은 PBR이면 ROE 높은 쪽이 싸다 (PBR 단독의 결함 보정)
  - EV/EBITDA: 부채·현금 조정 (자본구조 무관 비교)
  - FCF 수익률: 실제 현금 창출력 (회계이익 조작 무관)
  - PEG: 성장 대비 PER (성장주에 PER 단독의 부당함 보정)

A(퀄리티)·B2(발생액) 2연속 기각 후이므로, 기대를 낮추고 **IC + 분위 단조성 + 상위N 실측**
3종으로 엄격히 판정한다.

실행: python scripts/valuation_factor.py
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
VCACHE = ROOT / "data" / "us_valuation.pkl"
TOPK, COST = 20, 0.0005


def load_valuation(symbols):
    """yfinance info에서 밸류에이션 지표 (캐시). 현재 스냅샷 — look-ahead 유의."""
    if VCACHE.exists():
        d = pickle.loads(VCACHE.read_bytes())
        if len(d) >= len(symbols) * 0.5:
            return d
    import yfinance as yf

    rows = {}
    for i, sym in enumerate(symbols):
        try:
            info = yf.Ticker(sym).info
            mcap = info.get("marketCap") or 0
            rows[sym] = {
                "pe": info.get("trailingPE"),
                "fwd_pe": info.get("forwardPE"),
                "pb": info.get("priceToBook"),
                "roe": info.get("returnOnEquity"),
                "ev_ebitda": info.get("enterpriseToEbitda"),
                "peg": info.get("pegRatio") or info.get("trailingPegRatio"),
                "fcf_yield": ((info.get("freeCashflow") or 0) / mcap) if mcap else None,
                "ps": info.get("priceToSalesTrailing12Months"),
            }
        except Exception:
            pass
        if i % 50 == 0:
            print(f"  밸류 수집 {i}/{len(symbols)}...", flush=True)
            time.sleep(0.4)
    d = pd.DataFrame(rows).T
    VCACHE.write_bytes(pickle.dumps(d))
    return d


def build_factors(v):
    """팩터화 (클수록 좋다 방향). 이상치는 분위로 완화."""
    def num(c):
        return pd.to_numeric(v[c], errors="coerce")

    f = {}
    pe, pb, roe = num("pe"), num("pb"), num("roe")
    f["저PER (1/PE)"] = (1 / pe.where(pe > 0))
    f["저PBR (1/PB)"] = (1 / pb.where(pb > 0))
    f["FCF수익률"] = num("fcf_yield")
    ev = num("ev_ebitda")
    f["저EV/EBITDA"] = (1 / ev.where(ev > 0))
    peg = num("peg")
    f["저PEG"] = (1 / peg.where(peg > 0))
    # PB-ROE: 같은 PBR이면 ROE 높은 쪽이 저평가 → ROE/PB (스킬의 매트릭스 아이디어를 스칼라화)
    f["PB-ROE (ROE/PB)"] = (roe / pb.where(pb > 0))
    return {k: s.dropna() for k, s in f.items()}


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
    v = load_valuation(list(px.columns))
    facs = build_factors(v)
    print("확보:", " · ".join(f"{k} {len(s)}종목" for k, s in facs.items()))

    ret63 = px / px.shift(63) - 1
    s63 = spy / spy.shift(63) - 1
    mom = ret63.sub(s63.reindex(px.index), axis=0)
    trend = px > px.rolling(50).mean()

    # ---- 1) 팩터 자체 IC + 분위 단조성 (U자 감지 — 2026-07-27 교훈) ----
    print(f"\n=== 밸류 팩터 IC (forward 63일) + 분위별 수익 ===")
    fwd = px.shift(-63) / px - 1
    for name, s in facs.items():
        ics, qms = [], []
        for i in range(260, len(px) - 64, 5):
            b = fwd.iloc[i]
            m = b.notna() & b.index.isin(s.index)
            if m.sum() < 50:
                continue
            a = s.reindex(b[m].index)
            ics.append(a.rank().corr(b[m].rank()))
            lab = pd.qcut(a.rank(method="first"), 5, labels=False, duplicates="drop")
            qms.append(b[m].groupby(lab).mean())
        ic = pd.Series(ics).dropna()
        if len(ic) < 20:
            print(f"  {name:20} (표본부족)")
            continue
        qm = pd.DataFrame(qms).mean()
        ir = ic.mean() / ic.std() if ic.std() else 0
        verdict = ("유효" if abs(ic.mean()) >= 0.03 and abs(ir) >= 0.5 else
                   "약함" if abs(ic.mean()) >= 0.02 else "무의미")
        print(f"  {name:20} IC {ic.mean():+.4f} · IR {ir:+.2f} · {verdict:6} | "
              + " ".join(f"Q{i+1} {x*100:+4.1f}%" for i, x in enumerate(qm)))

    # ---- 2) 모멘텀에 밸류 필터 씌우기 (실전 조합) ----
    print(f"\n=== 모멘텀 + 밸류 필터 (상위50%만 매수) ===")
    print(f"{'전략':28}{'총수익':>9}{'CAGR':>8}{'MDD':>8}{'샤프':>7}")
    base = run(px, mom, trend)
    t_, cg, dd, sh = stats(base)
    print(f"{'모멘텀 단독(기준)':28}{t_:>+9.0%}{cg:>+8.1%}{dd:>+8.1%}{sh:>7.2f}")
    for name, s in facs.items():
        keep = s[s >= s.quantile(0.5)].index          # 밸류 상위50%(싼 쪽)
        t_, cg, dd, sh = stats(run(px, mom, trend, mask=keep))
        print(f"{'+ ' + name:28}{t_:>+9.0%}{cg:>+8.1%}{dd:>+8.1%}{sh:>7.2f}")
    print("\n※ 밸류는 현재 스냅샷 — look-ahead 편향으로 과대평가. 그럼에도 모멘텀 단독을")
    print("  못 이기면 실전 가치 없음(A 퀄리티·B2 발생액과 동일 논리)")


if __name__ == "__main__":
    main()
