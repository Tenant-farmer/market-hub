"""PEAD 검증 — 실적 서프라이즈 후 주가가 정말 표류하는가.

배경(Vibe-Trading earnings-revision 스킬 기준): PEAD는 주식시장에서 가장 견고한 아노말리로
알려져 있다. 문서가 제시한 정량 기준:
  - SUE(표준화 서프라이즈) > +2 = 강한 양의 신호, < -2 = 강한 음의 신호
  - 상위 분위 60일 드리프트 +4~8%, 하위 분위 -4~8%
  - 강화 필터: 소형주 > 대형주, 첫 서프라이즈 > 연속 서프라이즈

우리 검증 방식(데이터 제약 반영):
  yfinance의 분기 실적 서프라이즈(actual vs estimate)를 수집 → 발표 익일부터 N일 수익률.
  SUE 대신 **서프라이즈율**(surprise/|estimate|)로 분위를 나눈다(예측오차 표준편차 미보유).
  체결은 발표 다음날 종가(룩어헤드 차단).

실행: python scripts/pead_backtest.py
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
ECACHE = ROOT / "data" / "us_earnings_hist.pkl"
HORIZONS = (5, 21, 63)          # 1주 / 1개월 / 3개월(문서의 60~90일 드리프트 구간)


def load_earnings(symbols, limit=None):
    """yfinance 분기 실적 서프라이즈 이력 수집 (캐시)."""
    if ECACHE.exists():
        d = pickle.loads(ECACHE.read_bytes())
        if len(d) >= len(symbols) * 0.5:
            return d
    import yfinance as yf

    syms = symbols[:limit] if limit else symbols
    rows = []
    for i, sym in enumerate(syms):
        try:
            df = yf.Ticker(sym).earnings_history
            if df is None or len(df) == 0:
                continue
            df = df.reset_index()
            for _, r in df.iterrows():
                act = r.get("epsActual")
                est = r.get("epsEstimate")
                dt = r.get("quarter") if "quarter" in r else r.get("index")
                if act is None or est is None or pd.isna(act) or pd.isna(est) or est == 0:
                    continue
                rows.append({"symbol": sym, "date": pd.Timestamp(dt),
                             "actual": float(act), "estimate": float(est),
                             "surprise_pct": (float(act) - float(est)) / abs(float(est))})
        except Exception:
            pass
        if i % 50 == 0:
            print(f"  실적 수집 {i}/{len(syms)}...", flush=True)
            time.sleep(0.3)
    d = pd.DataFrame(rows)
    ECACHE.write_bytes(pickle.dumps(d))
    return d


def main():
    px, spy = load_cache()
    px = px.loc[:, px.notna().sum() >= 300]
    print(f"유니버스 {px.shape[1]}종목 · {px.index[0].date()}~{px.index[-1].date()}")
    er = load_earnings(list(px.columns))
    if er is None or len(er) == 0:
        sys.exit("[중단] 실적 데이터 없음")
    er = er[er["symbol"].isin(px.columns)].copy()
    print(f"실적 이벤트 {len(er)}건 · {er['symbol'].nunique()}종목 · "
          f"{er['date'].min().date()}~{er['date'].max().date()}")

    # 발표 다음 거래일 인덱스 매핑 (룩어헤드 차단)
    dates = px.index
    spy_ret = {}
    recs = []
    for _, r in er.iterrows():
        pos = dates.searchsorted(r["date"], side="right")     # 발표일 다음 거래일
        if pos < 260 or pos >= len(dates) - max(HORIZONS) - 1:
            continue
        sym = r["symbol"]
        c = px[sym]
        base = c.iloc[pos]
        if pd.isna(base) or base <= 0:
            continue
        rec = {"symbol": sym, "pos": pos, "surprise": r["surprise_pct"]}
        for H in HORIZONS:
            fw = c.iloc[pos + H]
            sp0, sp1 = spy.reindex(dates).ffill().iloc[pos], spy.reindex(dates).ffill().iloc[pos + H]
            if pd.notna(fw) and pd.notna(sp0) and sp0 > 0:
                rec[f"r{H}"] = fw / base - 1                       # 절대 수익
                rec[f"a{H}"] = (fw / base - 1) - (sp1 / sp0 - 1)   # 시장초과(알파)
        recs.append(rec)
    d = pd.DataFrame(recs)
    print(f"백테스트 가능 이벤트 {len(d)}건\n")

    # ---- 분위별 드리프트 (문서 기준: Q5 +4~8%, Q1 -4~8%) ----
    d["q"] = pd.qcut(d["surprise"].rank(method="first"), 5, labels=[1, 2, 3, 4, 5])
    print("=== 서프라이즈 분위별 발표 후 드리프트 (시장초과) ===")
    print(f"{'분위':8}{'N':>7}{'서프라이즈중앙':>14}" + "".join(f"{'+' + str(h) + '일':>10}" for h in HORIZONS))
    for q in (1, 2, 3, 4, 5):
        s = d[d["q"] == q]
        row = f"Q{q}{'(최악)' if q == 1 else '(최고)' if q == 5 else '':6}{len(s):>7}{s['surprise'].median():>13.1%}"
        for H in HORIZONS:
            col = f"a{H}"
            row += f"{s[col].mean() * 100:>+9.2f}%" if col in s else f"{'–':>10}"
        print(row)
    spread = {H: d[d["q"] == 5][f"a{H}"].mean() - d[d["q"] == 1][f"a{H}"].mean() for H in HORIZONS}
    print("\nQ5-Q1 스프레드: " + " · ".join(f"+{H}일 {v*100:+.2f}%p" for H, v in spread.items()))

    # ---- 문서의 SUE 기준선 (>+2 / <-2 대응: 서프라이즈율 상하위 10%) ----
    hi, lo = d["surprise"].quantile(0.9), d["surprise"].quantile(0.1)
    print(f"\n=== 극단 서프라이즈 (상위10% >{hi:.1%} / 하위10% <{lo:.1%}) ===")
    for lab, s in (("대형 서프라이즈(+)", d[d["surprise"] >= hi]),
                   ("대형 미스(-)", d[d["surprise"] <= lo])):
        row = f"  {lab:18}N={len(s):>5}"
        for H in HORIZONS:
            v = s[f"a{H}"]
            row += f"  +{H}일 {v.mean()*100:+.2f}% (승률 {(v>0).mean():.0%})"
        print(row)

    # ---- 강화 필터 검증: 소형주 우위 (문서 주장) ----
    print("\n=== 강화 필터: 소형 vs 대형 (문서 주장: 소형이 드리프트 큼) ===")
    try:
        from src import db
        con = db.connect()
        mcap = {r["symbol"]: r["mcap"] for r in con.execute("SELECT symbol, mcap FROM stock_meta")}
        con.close()
        d["mcap"] = d["symbol"].map(mcap)
        med = d["mcap"].median()
        for lab, s in (("소형(중앙값 이하)", d[d["mcap"] <= med]), ("대형(중앙값 초과)", d[d["mcap"] > med])):
            top = s[s["surprise"] >= s["surprise"].quantile(0.8)]
            print(f"  {lab:18}상위20% 서프라이즈 N={len(top):>5}  "
                  + "  ".join(f"+{H}일 {top[f'a{H}'].mean()*100:+.2f}%" for H in HORIZONS))
    except Exception as e:
        print("  (시총 데이터 없음:", str(e)[:40], ")")

    print("\n※ 판정: Q5-Q1 스프레드가 양수·단조면 PEAD 실재. 문서 기대치는 60일 +4~8%")


if __name__ == "__main__":
    main()
