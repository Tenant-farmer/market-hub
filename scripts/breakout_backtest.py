"""브레이크아웃(N일 신고가 돌파) 백테스트 — 추세추종.

전략: 종가가 최근 N일 신고가 돌파 AND 종가>50MA → 진입(다음날 종가 체결).
청산: 종가<20MA(추세이탈) / 손절 -SL% / 최대보유일 경과 → 다음날 종가.
추세 유지하는 동안 계속 보유(긴 홀드 허용).

파라미터 그리드: 신고가 룩백 N(20/55/252), 손절(-6/-8/-10%), 최대보유(20/40/60).

1단계(트레이드 통계): 종목별 독립 트레이드 나열 → 승률·기대값·평균보유.
2단계(자본곡선): 동시보유 최대 10슬롯 균등가중 포트폴리오 → 총수익/CAGR/MDD/샤프.
룩어헤드 방지: 신호는 당일 종가로 판정, 체결은 다음날 종가.

실행: PYTHONIOENCODING=utf-8 ./.venv/Scripts/python.exe scripts/breakout_backtest.py
"""
import pickle
from pathlib import Path

import numpy as np
import pandas as pd

COST_ONEWAY = 0.0005          # 편도 5bp → 왕복 0.1%
COST_RT = 2 * COST_ONEWAY
SLOTS = 10                    # 동시보유 최대 슬롯
MIN_HIST = 250               # 유니버스 필터: 250일 이상 데이터
WARM = 252                   # 최장 룩백(252) 확보 후 스캔 시작
CACHE = Path(__file__).resolve().parents[1] / "data" / "us_px_cache.pkl"


def load():
    px, spy = pickle.loads(CACHE.read_bytes())
    px = px.loc[:, px.notna().sum() >= MIN_HIST]   # 250일 이상만
    return px, spy


def resolve(c, m20, i, sl, hold, n):
    """신호 인덱스 i(당일 종가 판정) → 트레이드 해소.
    진입=다음날 종가 c[i+1]. 이후 종가<20MA / -sl / hold일 → 다음날 종가 청산."""
    entry = c[i + 1]
    if not np.isfinite(entry) or entry <= 0:
        return None
    j = i + 1
    while j < n - 1 and (j - (i + 1)) < hold:
        r = c[j] / entry - 1
        if r <= -sl or c[j] < m20[j]:
            exit_px = c[j + 1]
            hold_d = j - i
            ret = (exit_px / entry - 1) - COST_RT
            return {"entry_i": i + 1, "exit_i": j + 1, "entry": entry,
                    "exit": exit_px, "hold": hold_d, "ret": ret, "next": j + 1}
        j += 1
    ej = min(j + 1, n - 1)
    exit_px = c[ej]
    ret = (exit_px / entry - 1) - COST_RT
    return {"entry_i": i + 1, "exit_i": ej, "entry": entry, "exit": exit_px,
            "hold": j - i, "ret": ret, "next": ej}


def stage1_trades(cols, C, M20, SIG, sl, hold, n):
    """종목별 독립 트레이드 나열(청산 후 재진입 허용)."""
    out = []
    for k in range(len(cols)):
        c, m20, sg = C[k], M20[k], SIG[k]
        i = WARM
        while i < n - 1:
            if sg[i]:
                t = resolve(c, m20, i, sl, hold, n)
                if t is not None:
                    out.append(t)
                    i = t["next"] + 1
                    continue
            i += 1
    return out


def stage2_equity(cols, C, M20, SIG, R1, dates, sl, hold, n):
    """10슬롯 균등가중 포트폴리오 자본곡선.
    신호 발생 & 빈 슬롯 있으면 진입, 청산 시 슬롯 반환. 진입/청산에 편도비용."""
    cash = 1.0
    pos = {}                              # k -> {val, exit_i}
    eq = np.empty(n)
    eq[:WARM] = 1.0
    ncol = len(cols)
    for i in range(WARM, n):
        # 1) 보유 포지션 당일 수익 반영
        for k, p in pos.items():
            r = R1[k][i]
            if np.isfinite(r):
                p["val"] *= (1 + r)
        # 2) 청산 (exit_i == i)
        for k in [k for k, p in pos.items() if p["exit_i"] == i]:
            cash += pos[k]["val"] * (1 - COST_ONEWAY)
            del pos[k]
        # 3) 진입 (전일 i-1 신호, 빈 슬롯)
        avail = SLOTS - len(pos)
        if avail > 0 and i > WARM:
            equity_now = cash + sum(p["val"] for p in pos.values())
            slot_cap = equity_now / SLOTS
            for k in range(ncol):
                if avail <= 0 or cash <= 1e-12:
                    break
                if k in pos:
                    continue
                if SIG[k][i - 1]:
                    t = resolve(C[k], M20[k], i - 1, sl, hold, n)
                    if t is None:
                        continue
                    invest = min(slot_cap, cash)
                    cash -= invest
                    pos[k] = {"val": invest * (1 - COST_ONEWAY), "exit_i": t["exit_i"]}
                    avail -= 1
        eq[i] = cash + sum(p["val"] for p in pos.values())
    return pd.Series(eq, index=dates)


def curve_stats(c):
    c = c.loc[c.index[WARM]:]
    r = c.pct_change().dropna()
    yrs = (c.index[-1] - c.index[0]).days / 365.25
    tot = c.iloc[-1] / c.iloc[0] - 1
    cagr = (c.iloc[-1] / c.iloc[0]) ** (1 / yrs) - 1
    mdd = (c / c.cummax() - 1).min()
    sharpe = r.mean() / r.std() * np.sqrt(252) if r.std() else 0.0
    return tot, cagr, mdd, sharpe


def main():
    px, spy = load()
    n = len(px)
    dates = px.index
    cols = list(px.columns)
    print(f"유니버스 {len(cols)}종목 · {dates[0].date()}~{dates[-1].date()} · {n}일")

    # 지표 (벡터화)
    ma20 = px.rolling(20).mean()
    ma50 = px.rolling(50).mean()
    R1 = px.pct_change()

    C = [px[s].values for s in cols]
    M20 = [ma20[s].values for s in cols]
    R1a = [R1[s].values for s in cols]

    # N별 신고가 돌파 신호: close > 직전 N일 최고 종가 AND close > 50MA
    SIG_BY_N = {}
    for N in (20, 55, 252):
        highN = px.rolling(N).max().shift(1)
        sig = (px > highN) & (px > ma50)
        SIG_BY_N[N] = [sig[s].values for s in cols]

    # SPY 벤치마크 (동일 구간)
    spy_c = spy.reindex(dates).loc[dates[WARM]:]
    stot, scagr, smdd, ssharpe = curve_stats(spy.reindex(dates))
    print(f"\nSPY 단순보유 (동일구간): 총수익 {stot:+.1%} · CAGR {scagr:+.1%} · "
          f"MDD {smdd:+.1%} · 샤프 {ssharpe:.2f}\n")

    grid = [(N, sl, h) for N in (20, 55, 252)
            for sl in (0.06, 0.08, 0.10) for h in (20, 40, 60)]

    hdr = (f"{'규칙 (N·SL·HOLD)':22}{'N트레이드':>9}{'승률':>7}{'기대값':>9}{'보유일':>7}"
           f"{'│총수익':>10}{'CAGR':>8}{'MDD':>8}{'샤프':>7}")
    print(hdr)
    print("-" * len(hdr))

    results = []
    for (N, sl, h) in grid:
        SIG = SIG_BY_N[N]
        tr = stage1_trades(cols, C, M20, SIG, sl, h, n)
        rets = np.array([t["ret"] for t in tr])
        nt = len(tr)
        win = float((rets > 0).mean()) if nt else 0.0
        exp = float(rets.mean()) if nt else 0.0
        hold = float(np.mean([t["hold"] for t in tr])) if nt else 0.0

        eq = stage2_equity(cols, C, M20, SIG, R1a, dates, sl, h, n)
        tot, cagr, mdd, sharpe = curve_stats(eq)

        beats = (tot > stot) and (cagr > scagr) and (sharpe > ssharpe)
        results.append({"N": N, "sl": sl, "hold": h, "nt": nt, "win": win,
                        "exp": exp, "avg_hold": hold, "tot": tot, "cagr": cagr,
                        "mdd": mdd, "sharpe": sharpe, "beats": beats})
        lbl = f"N{N} SL{sl:.0%} H{h}"
        print(f"{lbl:22}{nt:>9}{win:>7.0%}{exp*100:>+8.2f}%{hold:>6.1f}일"
              f"{tot:>+10.0%}{cagr:>+8.1%}{mdd:>+8.1%}{sharpe:>7.2f}"
              f"{'  ✓SPY' if beats else ''}")

    # 최고 규칙 선정: 표본>=100 & 기대값>0 중, 자본곡선 총수익 최대 (SPY 초과 우선)
    elig = [r for r in results if r["nt"] >= 100 and r["exp"] > 0]
    if not elig:
        elig = [r for r in results if r["nt"] >= 100]
    beats_pool = [r for r in elig if r["beats"]]
    pool = beats_pool if beats_pool else elig
    best = max(pool, key=lambda r: r["tot"])

    print("\n" + "=" * 70)
    print(f"최고 규칙: N{best['N']} · 손절 -{best['sl']:.0%} · 최대보유 {best['hold']}일")
    print(f"  트레이드 {best['nt']}건 · 승률 {best['win']:.1%} · "
          f"기대값/트레이드 {best['exp']*100:+.2f}% (비용후) · 평균보유 {best['avg_hold']:.1f}일")
    print(f"  자본곡선: 총수익 {best['tot']:+.1%} · CAGR {best['cagr']:+.1%} · "
          f"MDD {best['mdd']:+.1%} · 샤프 {best['sharpe']:.2f}")
    print(f"  SPY 대비: 총수익 {best['tot']-stot:+.1%}p · CAGR {best['cagr']-scagr:+.1%}p · "
          f"샤프 {best['sharpe']-ssharpe:+.2f} → beats_spy={best['beats']}")

    # 스키마용 요약 출력 (파싱용)
    print("\n<<<SCHEMA>>>")
    print(f"best_rule=N{best['N']}_SL{int(best['sl']*100)}_HOLD{best['hold']}")
    print(f"n_trades={best['nt']}")
    print(f"win_rate={best['win']:.4f}")
    print(f"exp_per_trade_pct={best['exp']*100:.4f}")
    print(f"avg_hold_days={best['avg_hold']:.2f}")
    print(f"equity_total_return_pct={best['tot']*100:.2f}")
    print(f"cagr_pct={best['cagr']*100:.2f}")
    print(f"max_dd_pct={best['mdd']*100:.2f}")
    print(f"sharpe={best['sharpe']:.3f}")
    print(f"beats_spy={best['beats']}")
    print(f"spy_tot_pct={stot*100:.2f} spy_cagr_pct={scagr*100:.2f} "
          f"spy_mdd_pct={smdd*100:.2f} spy_sharpe={ssharpe:.3f}")


if __name__ == "__main__":
    main()
