"""평균회귀 극단(급락 반등) 백테스트 — mean-reversion extreme.

진입: RSI(2 or 3) < TH 극단 과매도  AND (200MA 필터 on이면 종가>200MA: 강세장 눌림)
청산: 종가>5MA 반등  OR  최대보유 HOLD일 경과  OR  손절 -SL%
     신호는 당일 종가까지 지표로 판정, 체결은 '다음날 종가' (룩어헤드 방지)
편도비용 COST=0.0005 (왕복 0.1%).

1단계 트레이드 통계(승률/기대값/보유일) + 2단계 자본곡선(동시보유 최대 10슬롯, 균등가중).
그리드 스윕 → 기대값 양호 & 자본곡선이 SPY에 근접/초과하는 최고 규칙 선정.
"""
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd

CACHE = Path(__file__).resolve().parents[1] / "data" / "us_px_cache.pkl"
COST = 0.0005          # 편도 (왕복 = 2*COST = 0.001)
SLOTS = 10
STOP = 0.05            # 손절 -5% (과제 고정)
START_I = 200          # ma200 워밍업 — 전 config 동일 기간 비교


def _rsi(s: pd.Series, n=14):
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def build_ind(px):
    """종목별 지표(close, rsi2, rsi3, ma5, ma200) → 마스터 인덱스 정수위치 매핑."""
    master = px.index
    pos = {d: i for i, d in enumerate(master)}
    ind = {}
    for sym in px.columns:
        c = px[sym].dropna()
        if len(c) < 250:
            continue
        ind[sym] = {
            "close": c.values,
            "rsi2": _rsi(c, 2).values,
            "rsi3": _rsi(c, 3).values,
            "ma5": c.rolling(5).mean().values,
            "ma200": c.rolling(200).mean().values,
            "gpos": np.array([pos[d] for d in c.index]),  # 각 로컬 i의 마스터 위치
        }
    return ind, master


def gen_trades(ind, rsi_period, rsi_th, ma_filter, hold):
    """규칙으로 모든 트레이드 나열.
    반환: (sym, entry_gpos, exit_gpos, entry_px, exit_px, ret_net, hold_days)
    """
    out = []
    rkey = "rsi2" if rsi_period == 2 else "rsi3"
    for sym, d in ind.items():
        c = d["close"]; rsi = d[rkey]; ma5 = d["ma5"]; ma200 = d["ma200"]; g = d["gpos"]
        n = len(c)
        i = START_I
        while i < n - 1:
            sig = (rsi[i] < rsi_th) and (not ma_filter or c[i] > ma200[i])
            if sig:
                entry = c[i + 1]                       # 다음날 종가 체결
                j = i + 1
                exit_px = None; exit_j = None
                while j < n - 1 and (j - (i + 1)) < hold:
                    r = c[j] / entry - 1
                    if c[j] > ma5[j] or r <= -STOP:    # 반등 or 손절 → 다음날 종가 청산
                        exit_px = c[j + 1]; exit_j = j + 1
                        break
                    j += 1
                if exit_px is None:                    # 최대보유 경과 → 다음날 종가
                    exit_j = min(j + 1, n - 1)
                    exit_px = c[exit_j]
                ret = (exit_px / entry - 1) - 2 * COST
                out.append((sym, g[i + 1], g[exit_j], entry, exit_px, ret, exit_j - (i + 1)))
                i = exit_j                             # 청산 후 재진입 가능(무겹침)
            else:
                i += 1
    return out


def trade_stats(tr):
    rets = np.array([t[5] for t in tr])
    hold = np.mean([t[6] for t in tr])
    win = float((rets > 0).mean())
    exp = float(rets.mean())
    return {"n": len(tr), "win": win, "exp": exp, "hold": float(hold),
            "std": float(rets.std())}


def equity_curve(tr, px_ff, master):
    """동시보유 최대 10슬롯, 균등가중. 일별 시가총액 마크 → 자본곡선."""
    ndays = len(master)
    # 날짜별 진입/청산 이벤트 버킷
    entries = {}   # gpos -> list of trade idx
    for k, t in enumerate(tr):
        entries.setdefault(t[1], []).append(k)
    # 심볼별 ffill 종가 배열(마스터 정렬)
    sym_px = {s: px_ff[s].values for s in px_ff.columns}

    cash = 1.0
    open_pos = {}   # trade_idx -> {"sym","shares","exit_gpos"}
    eq = np.empty(ndays)
    for day in range(ndays):
        # 1) 청산 먼저
        for k in [k for k, p in open_pos.items() if p["exit_gpos"] == day]:
            p = open_pos.pop(k)
            px_now = sym_px[p["sym"]][day]
            cash += p["shares"] * px_now * (1 - COST)
        # 2) 진입 (빈 슬롯 있으면)
        for k in entries.get(day, []):
            if len(open_pos) >= SLOTS or cash <= 1e-12:
                continue
            # 현재 총자산(현금+오픈 마크) 기준 균등 1/10, 단 현금 초과 불가
            v_open = sum(op["shares"] * sym_px[op["sym"]][day] for op in open_pos.values())
            equity_now = cash + v_open
            size = min(equity_now / SLOTS, cash)
            t = tr[k]
            entry_px = t[3]
            shares = size / entry_px
            cash -= size + size * COST                 # 진입비용
            open_pos[k] = {"sym": t[0], "shares": shares, "exit_gpos": t[2]}
        # 3) 일별 총자산 마크
        v_open = sum(op["shares"] * sym_px[op["sym"]][day] for op in open_pos.values())
        eq[day] = cash + v_open
    return pd.Series(eq, index=master)


def perf(eq, master):
    total = eq.iloc[-1] / eq.iloc[0] - 1
    yrs = (master[-1] - master[0]).days / 365.25
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1
    dd = (eq / eq.cummax() - 1).min()
    ret = eq.pct_change().dropna()
    sharpe = (ret.mean() / ret.std() * np.sqrt(252)) if ret.std() else 0.0
    return {"total": float(total), "cagr": float(cagr), "mdd": float(dd),
            "sharpe": float(sharpe)}


def main():
    px, spy = pickle.loads(CACHE.read_bytes())
    px = px.loc[:, px.notna().sum() >= 250]
    px_ff = px.ffill()
    master = px.index

    # SPY 벤치마크
    bh = spy.iloc[-1] / spy.iloc[0] - 1
    yrs = (master[-1] - master[0]).days / 365.25
    spy_cagr = (spy.iloc[-1] / spy.iloc[0]) ** (1 / yrs) - 1
    spy_dd = (spy / spy.cummax() - 1).min()
    spy_ret = spy.pct_change().dropna()
    spy_sharpe = spy_ret.mean() / spy_ret.std() * np.sqrt(252)
    print(f"SPY B&H: total {bh:+.1%} CAGR {spy_cagr:+.2%} MDD {spy_dd:+.1%} Sharpe {spy_sharpe:.2f}\n")

    ind, master = build_ind(px)
    print(f"유니버스 {len(ind)}종목 · {master[0].date()}~{master[-1].date()}\n")

    hdr = f"{'규칙':30}{'N':>6}{'승률':>7}{'기대값':>9}{'보유':>7}{'총수익':>9}{'CAGR':>8}{'MDD':>8}{'Sharpe':>8}"
    print(hdr)
    rows = []
    for rsi_period in (2, 3):
        for rsi_th in (5, 10, 15):
            for ma_filter in (True, False):
                for hold in (3, 5, 10):
                    tr = gen_trades(ind, rsi_period, rsi_th, ma_filter, hold)
                    if len(tr) < 100:
                        continue
                    st = trade_stats(tr)
                    eq = equity_curve(tr, px_ff, master)
                    pf = perf(eq, master)
                    lbl = f"RSI{rsi_period}<{rsi_th} MA200{'on' if ma_filter else 'off'} H{hold}"
                    print(f"{lbl:30}{st['n']:>6}{st['win']:>7.0%}{st['exp']*100:>+8.2f}%"
                          f"{st['hold']:>6.1f}d{pf['total']:>+8.0%}{pf['cagr']:>+8.1%}"
                          f"{pf['mdd']:>+8.0%}{pf['sharpe']:>8.2f}")
                    rows.append((lbl, st, pf))

    # 선정: 기대값>0 & 표본>=100 중, 자본곡선 총수익이 SPY 근접/초과하며 기대값·Sharpe 우수
    # 스코어 = 자본곡선 총수익 (SPY 대비) 우선, 동률시 기대값
    print("\n=== 후보 랭킹 (자본곡선 총수익 기준 상위) ===")
    ranked = sorted([r for r in rows if r[1]["exp"] > 0],
                    key=lambda r: r[2]["total"], reverse=True)
    for lbl, st, pf in ranked[:8]:
        beats = pf["total"] > bh and pf["cagr"] > spy_cagr and pf["sharpe"] > spy_sharpe
        print(f"{lbl:30} exp {st['exp']*100:+.2f}% N{st['n']} | "
              f"total {pf['total']:+.0%} CAGR {pf['cagr']:+.1%} Sharpe {pf['sharpe']:.2f} "
              f"MDD {pf['mdd']:+.0%} beats_SPY={beats}")

    best = ranked[0]
    lbl, st, pf = best
    beats = pf["total"] > bh and pf["cagr"] > spy_cagr and pf["sharpe"] > spy_sharpe
    print("\n=== BEST ===")
    print(f"{lbl}")
    print(f"trades={st['n']} win={st['win']:.3f} exp/trade={st['exp']*100:+.3f}% hold={st['hold']:.2f}d")
    print(f"equity total={pf['total']*100:+.2f}% CAGR={pf['cagr']*100:+.2f}% "
          f"MDD={pf['mdd']*100:+.2f}% Sharpe={pf['sharpe']:.3f}")
    print(f"SPY total={bh*100:+.2f}% CAGR={spy_cagr*100:+.2f}% Sharpe={spy_sharpe:.3f}")
    print(f"beats_spy(total&cagr&sharpe)={beats}")
    print(f"beats_total={pf['total']>bh} beats_cagr={pf['cagr']>spy_cagr} beats_sharpe={pf['sharpe']>spy_sharpe}")


if __name__ == "__main__":
    main()
