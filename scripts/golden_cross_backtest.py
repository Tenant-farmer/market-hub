"""골든크로스 단기 백테스트 — 미국 주도주 유니버스(캐시).

전략(골든크로스 단기):
- 진입: 단기MA(short)가 중기MA(mid) 상향 돌파(크로스업). 신호는 당일 종가까지 지표로 판정,
        체결은 '다음날 종가'(룩어헤드 방지).
- 청산: 데드크로스(short<mid 하향) / 손절 -SL% / 최대보유 HOLD(거래일). 청산도 다음날 종가.
- 그리드: short(5/10/20) x mid(30/50/100, short<mid) x SL(0.05/0.08/0.12), HOLD=60.

산출:
  1) 트레이드 통계(슬롯 무제한 나열): 총 트레이드, 승률, 트레이드당 기대값(%, 비용후), 평균 보유일
  2) 자본곡선(동시보유 최대 10슬롯 균등가중, 자기자본 분할): 총수익%, CAGR%, MaxDD%, Sharpe
  3) SPY 대비 판정
"""
import pickle
from pathlib import Path

import numpy as np
import pandas as pd

COST = 0.0005          # 편도 거래비용 (왕복 0.1%)
ROUNDTRIP = 2 * COST
HOLD = 60              # 최대보유 거래일
MAX_SLOTS = 10
MIN_HIST = 250        # 유니버스 필터: 250일 이상
CACHE = Path(__file__).resolve().parents[1] / "data" / "us_px_cache.pkl"


def load():
    px, spy = pickle.loads(CACHE.read_bytes())
    px = px.loc[:, px.notna().sum() >= MIN_HIST]
    return px, spy


def build_ind(px, short, mid):
    """종목별 close/maS/maL 배열 (공통 날짜축 정렬, np.array)."""
    n = len(px.index)
    ind = {}
    for sym in px.columns:
        c = px[sym]
        if c.notna().sum() < MIN_HIST:
            continue
        maS = c.rolling(short).mean()
        maL = c.rolling(mid).mean()
        ind[sym] = (c.values, maS.values, maL.values)
    return ind


def _cross_up(maS, maL, i):
    """i일 크로스업: 전일 short<=mid, 당일 short>mid (모두 유효)."""
    a0, b0, a1, b1 = maS[i - 1], maL[i - 1], maS[i], maL[i]
    if np.isnan(a0) or np.isnan(b0) or np.isnan(a1) or np.isnan(b1):
        return False
    return a0 <= b0 and a1 > b1


def list_trades(ind, mid, sl):
    """슬롯 무제한 — 모든 (종목,진입) 트레이드 나열. 각 트레이드 (sym, entry_k, hold_days, ret_after_cost)."""
    out = []
    for sym, (c, maS, maL) in ind.items():
        n = len(c)
        i = mid + 1
        while i < n - 1:
            if _cross_up(maS, maL, i):
                ek = i + 1                       # 다음날 종가 체결
                entry = c[ek]
                if np.isnan(entry):
                    i += 1
                    continue
                j = ek
                exit_k = None
                while j < n - 1:
                    held = j - ek
                    r = c[j] / entry - 1
                    dead = (not np.isnan(maS[j]) and not np.isnan(maL[j]) and maS[j] < maL[j])
                    if r <= -sl or dead or held >= HOLD:
                        exit_k = j + 1           # 조건 충족 → 다음날 종가 청산
                        break
                    j += 1
                if exit_k is None:
                    exit_k = n - 1
                exit_px = c[exit_k]
                ret = (exit_px / entry - 1) - ROUNDTRIP
                out.append((sym, ek, exit_k - ek, ret))
                i = exit_k + 1                   # 청산 후 재진입 가능
            else:
                i += 1
    return out


def equity_curve(ind, dates, mid, sl):
    """동시보유 최대 10슬롯, 자기자본 분할(빈 슬롯 균등). 신호 다음날 종가 체결.
    반환: 날짜별 equity Series."""
    syms = list(ind.keys())
    n = len(dates)
    cash = 1.0
    positions = {}          # sym -> dict(entry_price, entry_k, shares)
    pend_buy = {}           # exec_k -> [sym,...]
    pend_sell = {}          # exec_k -> [sym,...]
    scheduled_exit = set()  # syms already scheduled to sell
    equity = np.empty(n)
    equity[:] = np.nan

    for k in range(n):
        # 1) 오늘 예정된 매도 체결 (전일 결정)
        for sym in pend_sell.pop(k, []):
            if sym in positions:
                p = positions.pop(sym)
                px_k = ind[sym][0][k]
                proceeds = p["shares"] * px_k
                cash += proceeds * (1 - COST)
                scheduled_exit.discard(sym)
        # 2) 오늘 예정된 매수 체결 (빈 슬롯 있으면)
        for sym in pend_buy.pop(k, []):
            if sym in positions:
                continue
            free = MAX_SLOTS - len(positions)
            if free <= 0:
                continue
            px_k = ind[sym][0][k]
            if np.isnan(px_k) or px_k <= 0:
                continue
            alloc = cash / free                 # 빈 슬롯 균등분할(자기자본)
            if alloc <= 0:
                continue
            shares = alloc / px_k
            cash -= alloc
            cash -= alloc * COST                # 편도 매수비용
            positions[sym] = {"entry_price": px_k, "entry_k": k, "shares": shares}
        # 3) 보유 포지션 청산 판정 (오늘 지표) → 내일 매도 예약
        for sym, p in positions.items():
            if sym in scheduled_exit:
                continue
            c, maS, maL = ind[sym]
            px_k = c[k]
            if np.isnan(px_k):
                continue
            held = k - p["entry_k"]
            r = px_k / p["entry_price"] - 1
            dead = (not np.isnan(maS[k]) and not np.isnan(maL[k]) and maS[k] < maL[k])
            if r <= -sl or dead or held >= HOLD:
                if k + 1 < n:
                    pend_sell.setdefault(k + 1, []).append(sym)
                    scheduled_exit.add(sym)
        # 4) 신규 진입 신호(오늘 크로스업) → 내일 매수 예약
        if MAX_SLOTS - len(positions) - sum(len(v) for kk, v in pend_buy.items() if kk == k + 1) > 0:
            for sym in syms:
                if sym in positions:
                    continue
                c, maS, maL = ind[sym]
                if k >= mid + 1 and _cross_up(maS, maL, k):
                    if k + 1 < n:
                        pend_buy.setdefault(k + 1, []).append(sym)
        # 5) equity 마킹 (오늘 종가)
        val = cash
        for sym, p in positions.items():
            px_k = ind[sym][0][k]
            if not np.isnan(px_k):
                val += p["shares"] * px_k
        equity[k] = val

    return pd.Series(equity, index=dates)


def curve_stats(eq, dates):
    tot = eq.iloc[-1] / eq.iloc[0] - 1
    yrs = (dates[-1] - dates[0]).days / 365.25
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1
    roll_max = eq.cummax()
    mdd = (eq / roll_max - 1).min()
    dret = eq.pct_change().dropna()
    sharpe = dret.mean() / dret.std() * np.sqrt(252) if dret.std() > 0 else 0.0
    return tot, cagr, mdd, sharpe


def main():
    px, spy = load()
    dates = px.index
    yrs = (dates[-1] - dates[0]).days / 365.25
    spy_tot = spy.iloc[-1] / spy.iloc[0] - 1
    spy_cagr = (1 + spy_tot) ** (1 / yrs) - 1
    spy_ret = spy.pct_change().dropna()
    spy_sharpe = spy_ret.mean() / spy_ret.std() * np.sqrt(252)
    spy_mdd = (spy / spy.cummax() - 1).min()
    print(f"유니버스 {px.shape[1]}종목 · {dates[0].date()}~{dates[-1].date()} ({yrs:.1f}y)")
    print(f"SPY B&H: 총수익 {spy_tot:+.1%}  CAGR {spy_cagr:+.1%}  MDD {spy_mdd:.1%}  Sharpe {spy_sharpe:.2f}\n")

    grid_short = (5, 10, 20)
    grid_mid = (30, 50, 100)
    grid_sl = (0.05, 0.08, 0.12)

    print(f"{'규칙':22}{'N':>6}{'승률':>7}{'기대값':>9}{'보유일':>7}"
          f"{'총수익':>9}{'CAGR':>8}{'MDD':>8}{'Sharpe':>8}")
    rows = []
    ind_cache = {}
    for short in grid_short:
        for mid in grid_mid:
            if short >= mid:
                continue
            key = (short, mid)
            if key not in ind_cache:
                ind_cache[key] = build_ind(px, short, mid)
            ind = ind_cache[key]
            for sl in grid_sl:
                tr = list_trades(ind, mid, sl)
                n = len(tr)
                if n == 0:
                    continue
                rets = np.array([t[3] for t in tr])
                win = (rets > 0).mean()
                exp = rets.mean()
                hold = np.mean([t[2] for t in tr])
                eq = equity_curve(ind, dates, mid, sl)
                tot, cagr, mdd, sharpe = curve_stats(eq, dates)
                lbl = f"MA{short}/{mid} SL{sl:.0%}"
                print(f"{lbl:22}{n:>6}{win:>7.0%}{exp*100:>+8.2f}%{hold:>7.1f}"
                      f"{tot:>+8.0%}{cagr:>+7.1%}{mdd:>7.0%}{sharpe:>8.2f}")
                rows.append(dict(short=short, mid=mid, sl=sl, n=n, win=win, exp=exp,
                                 hold=hold, tot=tot, cagr=cagr, mdd=mdd, sharpe=sharpe))

    # 선정: n>=100 & 기대값>0 중 자본곡선 총수익 최대(=SPY 근접/초과)
    elig = [r for r in rows if r["n"] >= 100 and r["exp"] > 0]
    pool = elig if elig else [r for r in rows if r["n"] >= 100] or rows
    best = max(pool, key=lambda r: r["tot"])
    print("\n=== 최고 규칙 ===")
    print(f"MA{best['short']}/{best['mid']} SL{best['sl']:.0%} HOLD{HOLD}")
    print(f"N={best['n']}  승률={best['win']:.1%}  기대값/트레이드={best['exp']*100:+.2f}% (비용후)  "
          f"평균보유={best['hold']:.1f}일")
    print(f"자본곡선: 총수익 {best['tot']:+.1%}  CAGR {best['cagr']:+.1%}  "
          f"MDD {best['mdd']:.1%}  Sharpe {best['sharpe']:.2f}")
    beats = best["tot"] > spy_tot and best["cagr"] > spy_cagr
    print(f"beats_spy(총수익&CAGR): {beats}  | SPY대비 Sharpe {best['sharpe']:.2f} vs {spy_sharpe:.2f}")

    import json
    print("\nJSON_RESULT=" + json.dumps({
        "best": best,
        "spy": {"tot": spy_tot, "cagr": spy_cagr, "mdd": float(spy_mdd), "sharpe": spy_sharpe},
        "beats_spy": bool(beats),
    }, default=float))


if __name__ == "__main__":
    main()
