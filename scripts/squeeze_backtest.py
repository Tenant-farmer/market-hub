"""변동성 수축 돌파(스퀴즈) 백테스트 — 볼린저밴드 스퀴즈 후 상단 돌파.

가설: 볼린저밴드폭(20일)이 최근 6개월 최저 분위 이내로 수축(변동성 압축)한 뒤
      종가가 상단밴드를 돌파하면 추세 확장이 이어진다.

- 진입: bw20[i] <= 최근 126일 bw20 p분위(수축) AND 종가 > 상단밴드(돌파)
        → 신호 다음날 종가 체결 (룩어헤드 방지)
- 청산: 종가 < 20MA(추세이탈) / -SL 손절 / 최대보유 경과 → 다음날 종가 체결
- 파라미터 그리드: 밴드폭 분위(20/25/30%), 손절(5/7/10%), 최대보유(20/40일)
- 비용: 편도 COST=0.0005 (왕복 0.1%)

1단계 트레이드 통계(모든 규칙 트레이드, 종목내 비중복) + 2단계 자본곡선(동시 10슬롯).
실행: PYTHONIOENCODING=utf-8 ./.venv/Scripts/python.exe scripts/squeeze_backtest.py
"""
import pickle
from pathlib import Path

import numpy as np
import pandas as pd

COST = 0.0005                       # 편도 거래비용 (왕복 0.1%)
BB_N = 20                           # 볼린저 기간
BB_K = 2.0                          # 표준편차 배수
LOOKBACK = 126                      # 수축 분위 산출 창 (약 6개월)
MIN_DAYS = 250                      # 유니버스 필터
N_SLOTS = 10                        # 동시보유 최대 슬롯
CACHE = Path(__file__).resolve().parents[1] / "data" / "us_px_cache.pkl"


def load():
    px, spy = pickle.loads(CACHE.read_bytes())
    px = px.loc[:, px.notna().sum() >= MIN_DAYS]
    return px, spy


def build_indicators(px):
    """종목별 지표 DataFrame. 룩어헤드 없이 당일 종가까지만 사용."""
    ind = {}
    for sym in px.columns:
        c = px[sym].dropna()
        if len(c) < MIN_DAYS:
            continue
        ma20 = c.rolling(BB_N).mean()
        sd20 = c.rolling(BB_N).std(ddof=0)
        upper = ma20 + BB_K * sd20
        bw = (2 * BB_K * sd20) / ma20            # (upper-lower)/ma20 = 밴드폭(정규화)
        # 수축 임계: 최근 126일 bw 분위 (현재 포함, 미래 미참조)
        ind[sym] = pd.DataFrame({"close": c, "ma20": ma20, "upper": upper, "bw": bw})
    return ind


def gen_trades(ind, pctl, sl, max_hold):
    """규칙으로 종목별 비중복 트레이드 나열. 각 트레이드: (sym, entry_date, exit_date, hold_days, ret)."""
    out = []
    for sym, d in ind.items():
        c = d["close"].values
        ma20 = d["ma20"].values
        upper = d["upper"].values
        bw = d["bw"].values
        idx = d.index
        # 수축 임계 시계열 (종목 자체 bw의 rolling 분위)
        sq_th = d["bw"].rolling(LOOKBACK).quantile(pctl).values
        n = len(c)
        i = LOOKBACK                              # 분위 창 확보 후 시작
        while i < n - 1:
            squeeze = (not np.isnan(bw[i])) and (not np.isnan(sq_th[i])) and (bw[i] <= sq_th[i])
            breakout = (not np.isnan(upper[i])) and (c[i] > upper[i])
            if squeeze and breakout:
                entry = c[i + 1]                  # 다음날 종가 체결
                j = i + 1
                exit_px, exit_j = None, None
                while j < n - 1 and (j - (i + 1)) < max_hold:
                    r = c[j] / entry - 1
                    if r <= -sl or c[j] < ma20[j]:
                        exit_px, exit_j = c[j + 1], j + 1
                        break
                    j += 1
                if exit_px is None:
                    exit_j = min(j + 1, n - 1)
                    exit_px = c[exit_j]
                ret = (exit_px / entry - 1) - 2 * COST   # 왕복 비용
                hold_days = exit_j - (i + 1)
                out.append((sym, idx[i + 1], idx[exit_j], hold_days, ret))
                i = exit_j + 1                    # 청산 후 재진입 가능
            else:
                i += 1
    return out


def trade_stats(tr):
    if not tr:
        return None
    rets = np.array([t[4] for t in tr])
    hold = np.array([t[3] for t in tr])
    return {
        "n": len(tr),
        "win": float((rets > 0).mean()),
        "exp": float(rets.mean()),
        "median": float(np.median(rets)),
        "hold": float(hold.mean()),
        "std": float(rets.std()),
    }


def equity_curve(tr, px, dates):
    """동시 10슬롯 균등가중 일별 마크투마켓 자본곡선.
    슬롯 free일 때 현금, 신호 발생시 빈 슬롯 있으면 진입, 청산일에 슬롯 반환."""
    # 진입/청산 이벤트를 실행일 기준으로 인덱싱
    entries_by_date = {}
    for t in tr:
        entries_by_date.setdefault(t[1], []).append(t)
    # 각 슬롯: None(현금) 또는 dict(position)
    books = [{"cash": 1.0 / N_SLOTS, "pos": None} for _ in range(N_SLOTS)]
    # 진행중 포지션의 청산일 매핑
    equity_series = []
    px_ff = px.ffill()

    for date in dates:
        # 1) 청산 처리 (오늘 종가 매도) — 슬롯 반환 먼저
        for b in books:
            p = b["pos"]
            if p is not None and p["exit_date"] == date:
                exit_px = p["exit_price"]
                b["cash"] = p["shares"] * exit_px * (1 - COST)
                b["pos"] = None
        # 2) 진입 처리 (오늘 종가 매수) — 빈 슬롯에 배정
        todays = entries_by_date.get(date, [])
        if todays:
            todays = sorted(todays, key=lambda t: t[0])   # 심볼순 결정적
            for t in todays:
                # 빈 슬롯 탐색
                slot = next((b for b in books if b["pos"] is None), None)
                if slot is None:
                    continue                               # 슬롯 없음 → 신호 스킵
                entry_px = px_ff.at[date, t[0]]
                if not np.isfinite(entry_px) or entry_px <= 0:
                    continue
                cap = slot["cash"] * (1 - COST)
                slot["pos"] = {
                    "sym": t[0],
                    "shares": cap / entry_px,
                    "exit_date": t[2],
                    "exit_price": px_ff.at[t[2], t[0]],
                }
                slot["cash"] = 0.0
        # 3) 마크투마켓 (오늘 종가 기준 총가치)
        total = 0.0
        for b in books:
            if b["pos"] is None:
                total += b["cash"]
            else:
                px_now = px_ff.at[date, b["pos"]["sym"]]
                total += b["pos"]["shares"] * px_now
        equity_series.append(total)

    return pd.Series(equity_series, index=dates)


def curve_metrics(eq, dates):
    total = eq.iloc[-1] / eq.iloc[0] - 1
    yrs = (dates[-1] - dates[0]).days / 365.25
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1
    roll_max = eq.cummax()
    mdd = (eq / roll_max - 1).min()
    daily = eq.pct_change().dropna()
    sharpe = (daily.mean() / daily.std() * np.sqrt(252)) if daily.std() else 0.0
    return {"total": float(total), "cagr": float(cagr), "mdd": float(mdd),
            "sharpe": float(sharpe)}


def spy_bench(spy):
    total = spy.iloc[-1] / spy.iloc[0] - 1
    yrs = (spy.index[-1] - spy.index[0]).days / 365.25
    cagr = (spy.iloc[-1] / spy.iloc[0]) ** (1 / yrs) - 1
    roll_max = spy.cummax()
    mdd = (spy / roll_max - 1).min()
    daily = spy.pct_change().dropna()
    sharpe = (daily.mean() / daily.std() * np.sqrt(252)) if daily.std() else 0.0
    return {"total": float(total), "cagr": float(cagr), "mdd": float(mdd),
            "sharpe": float(sharpe)}


def main():
    px, spy = load()
    dates = px.index
    print(f"유니버스 {px.shape[1]}종목 · {dates[0].date()}~{dates[-1].date()}")
    b = spy_bench(spy)
    print(f"SPY 단순보유: 총수익 {b['total']:+.1%}  CAGR {b['cagr']:+.1%}  "
          f"MDD {b['mdd']:.1%}  Sharpe {b['sharpe']:.2f}\n")

    ind = build_indicators(px)

    hdr = (f"{'규칙 (pctl/SL/HOLD)':22}{'N':>5}{'승률':>6}{'기대값':>9}{'중앙':>8}"
           f"{'보유':>6}{'변동':>7} | {'총수익':>8}{'CAGR':>7}{'MDD':>7}{'Sharpe':>7}")
    print(hdr)
    print("-" * len(hdr))

    results = []
    for pctl in (0.20, 0.25, 0.30):
        for sl in (0.05, 0.07, 0.10):
            for max_hold in (20, 40):
                tr = gen_trades(ind, pctl, sl, max_hold)
                st = trade_stats(tr)
                if st is None:
                    continue
                eq = equity_curve(tr, px, dates)
                cm = curve_metrics(eq, dates)
                lbl = f"p{int(pctl*100)} SL{int(sl*100)} H{max_hold}"
                print(f"{lbl:22}{st['n']:>5}{st['win']:>6.0%}{st['exp']*100:>+8.2f}%"
                      f"{st['median']*100:>+7.2f}%{st['hold']:>5.1f}d{st['std']*100:>6.1f}%"
                      f" | {cm['total']:>+7.0%}{cm['cagr']:>+6.1%}{cm['mdd']:>6.1%}"
                      f"{cm['sharpe']:>7.2f}")
                results.append({"pctl": pctl, "sl": sl, "max_hold": max_hold,
                                "lbl": lbl, **st, **{f"eq_{k}": v for k, v in cm.items()}})

    # 최고 규칙 선정: 표본>=100, 기대값 양수 우선 → 자본곡선 총수익 최대
    elig = [r for r in results if r["n"] >= 100 and r["exp"] > 0]
    pool = elig if elig else [r for r in results if r["n"] >= 100]
    if not pool:
        pool = results
    best = max(pool, key=lambda r: (r["eq_total"], r["exp"]))
    print("\n=== 최고 규칙 ===")
    print(f"{best['lbl']}  (밴드폭 {int(best['pctl']*100)}분위 이내 수축 + 상단돌파, "
          f"SL -{int(best['sl']*100)}%, 최대보유 {best['max_hold']}일)")
    print(f"트레이드 N={best['n']}  승률={best['win']:.1%}  "
          f"기대값/트레이드={best['exp']*100:+.2f}% (비용후)  평균보유={best['hold']:.1f}일")
    print(f"자본곡선: 총수익={best['eq_total']:+.1%}  CAGR={best['eq_cagr']:+.1%}  "
          f"MDD={best['eq_mdd']:.1%}  Sharpe={best['eq_sharpe']:.2f}")
    beats = (best["eq_total"] > b["total"] and best["eq_cagr"] > b["cagr"]
             and best["eq_sharpe"] > b["sharpe"])
    print(f"beats_spy (총수익&CAGR&Sharpe 모두 초과): {beats}")

    # schema용 값 출력
    print("\n--- SCHEMA ---")
    import json
    print(json.dumps({
        "strategy": "변동성 수축 돌파(볼린저 스퀴즈+상단밴드 돌파)",
        "best_rule": f"밴드폭{int(best['pctl']*100)}분위 수축+상단돌파, SL-{int(best['sl']*100)}%, "
                     f"최대보유{best['max_hold']}일, BB(20,2), 편도비용0.05%",
        "n_trades": best["n"],
        "win_rate": round(best["win"], 4),
        "exp_per_trade_pct": round(best["exp"] * 100, 4),
        "avg_hold_days": round(best["hold"], 2),
        "equity_total_return_pct": round(best["eq_total"] * 100, 2),
        "cagr_pct": round(best["eq_cagr"] * 100, 2),
        "max_dd_pct": round(best["eq_mdd"] * 100, 2),
        "sharpe": round(best["eq_sharpe"], 3),
        "spy_total_pct": round(b["total"] * 100, 2),
        "spy_cagr_pct": round(b["cagr"] * 100, 2),
        "spy_sharpe": round(b["sharpe"], 3),
        "beats_spy": bool(beats),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
