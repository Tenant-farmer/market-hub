"""손절폭 검증 — EXIT_STOP_PCT = -8%가 최적인가.

우리 전략에서 유일하게 상시 작동하는 하방 보호가 종목별 손절이다. 그런데 **-8%라는 숫자는
검증된 적이 없다**(관례로 정한 값). 새 규칙을 더하는 게 아니라 이미 있는 규칙의 파라미터
하나를 재는 것이라 복잡도 증가가 없다 — 레짐 필터 기각 후 남은 가장 저렴한 개선 후보.

손절의 두 얼굴:
  - 좁으면(-5%) 정상 변동에 털려 나갔다가 도로 사야 한다(휩쏘). 이평선 문제와 같은 구조.
  - 넓으면(-20%) 보호가 늦어 이미 크게 맞은 뒤 나온다.
어디가 최적인지는 데이터로만 알 수 있다.

모델링:
  - 모멘텀 로테이션(126일 상위 10, 21일 리밸런스)에 **일별 손절 점검**을 얹는다
  - 손절가는 **최초 진입가 기준**(브로커 평단과 동일 — 리밸런스로 유지돼도 리셋 안 함)
  - 털린 슬롯은 다음 리밸런스까지 **현금**(빈 슬롯은 수익 0 — 재진입 안 함)
  - 종가 기준 판정. 실제 시스템은 60초마다 보므로 장중에 더 일찍·더 나쁘게 체결된다
    → 여기 결과는 **낙관 쪽으로 치우친 추정**임을 감안할 것

강건성: 레짐 필터를 뒤집은 **리밸런스 위상 21가지**를 처음부터 포함한다. 위상 하나로 낸
점추정은 믿지 않는다(2026-07-27 교훈).

실행: python scripts/stop_loss_sweep.py
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _bt_cache import load_cache  # noqa: E402

TOPK, REBAL, COST, WARM = 10, 21, 0.0010, 260      # COST=편도 10bp
STOPS = [None, -5.0, -8.0, -10.0, -12.0, -15.0, -20.0]
RECOVER_DAYS = 21          # 손절 후 이 기간 수익률로 '나온 게 옳았나' 판정


def prep():
    px, spy = load_cache()
    px = px.loc[:, px.notna().sum() >= 300]
    ret63 = px / px.shift(63) - 1
    s63 = spy / spy.shift(63) - 1
    score = ret63.sub(s63.reindex(px.index), axis=0).where(px > px.rolling(50).mean())
    return px, px.index, px.to_numpy(), score.to_numpy(), list(px.columns)


def run(P, S, n_days, n_sym, stop, phase=0, track=False):
    """자본곡선(np.array) + 손절 통계. 슬롯 단위 동일가중, 털린 슬롯은 현금."""
    equity = 1.0
    eq = np.empty(n_days)
    held = {}                       # {col_idx: entry_px}
    stops_hit, recovered = 0, 0
    for i in range(n_days):
        if i > 0 and held:
            row, prev = P[i], P[i - 1]
            r = 0.0
            for j in held:
                if np.isfinite(row[j]) and np.isfinite(prev[j]) and prev[j] > 0:
                    r += row[j] / prev[j] - 1
            equity *= (1 + r / TOPK)         # 빈 슬롯은 수익 0 (현금)
        if i < WARM:
            eq[i] = equity
            continue

        if stop is not None and held:        # ── 일별 손절 점검 ──
            for j in [j for j, e in held.items()
                      if np.isfinite(P[i][j]) and e > 0 and (P[i][j] / e - 1) * 100 <= stop]:
                del held[j]
                equity *= (1 - COST)
                stops_hit += 1
                if track and i + RECOVER_DAYS < n_days:
                    fwd = P[i + RECOVER_DAYS][j]
                    if np.isfinite(fwd) and fwd > P[i][j] * 1.05:
                        recovered += 1       # 털린 뒤 5%+ 반등 = 나온 게 손해였던 경우

        if (i - phase) % REBAL == 0:         # ── 리밸런스 ──
            sc = S[i]
            order = np.argsort(np.where(np.isfinite(sc), -sc, np.inf))
            top = [int(j) for j in order[:TOPK] if np.isfinite(sc[int(j)])]
            new = [j for j in top if j not in held]
            gone = [j for j in held if j not in top]
            if new or gone:
                equity *= (1 - COST * len(new + gone) / TOPK)
            for j in gone:
                del held[j]
            for j in new:
                if np.isfinite(P[i][j]) and P[i][j] > 0:
                    held[j] = P[i][j]        # 진입가 기록 (유지 종목은 리셋 안 함)
        eq[i] = equity
    return eq, stops_hit, recovered


def post_stop_returns(P, S, n_days, stop) -> np.ndarray:
    """손절 직후 RECOVER_DAYS 수익률 — 자른 게 옳았는지의 직접 증거."""
    held, out = {}, []
    for i in range(WARM, n_days):
        for j in [j for j, e in held.items()
                  if np.isfinite(P[i][j]) and e > 0 and (P[i][j] / e - 1) * 100 <= stop]:
            del held[j]
            if i + RECOVER_DAYS < n_days and np.isfinite(P[i + RECOVER_DAYS][j]):
                out.append((P[i + RECOVER_DAYS][j] / P[i][j] - 1) * 100)
        if i % REBAL == 0:
            sc = S[i]
            order = np.argsort(np.where(np.isfinite(sc), -sc, np.inf))
            top = [int(j) for j in order[:TOPK] if np.isfinite(sc[int(j)])]
            for j in [j for j in held if j not in top]:
                del held[j]
            for j in top:
                if j not in held and np.isfinite(P[i][j]) and P[i][j] > 0:
                    held[j] = P[i][j]
    return np.array(out)


def stats(eq, idx):
    c = pd.Series(eq, index=idx)
    c = c[WARM:]
    c = c / c.iloc[0]
    yrs = (c.index[-1] - c.index[0]).days / 365.25
    r = c.pct_change().dropna()
    cagr = c.iloc[-1] ** (1 / yrs) - 1
    mdd = (c / c.cummax() - 1).min()
    return {"cagr": cagr, "mdd": mdd, "calmar": cagr / abs(mdd) if mdd else 0,
            "sharpe": r.mean() / r.std() * np.sqrt(252) if r.std() else 0,
            "worst_yr": c.resample("YE").last().pct_change().min(), "yrs": yrs}


def main():
    px, idx, P, S, cols = prep()
    n_days, n_sym = P.shape
    print(f"=== 손절폭 검증 ({n_sym}종목 · {idx[0].date()}~{idx[-1].date()}) ===")
    print("  모멘텀 로테이션(126일 top10, 21일 리밸런스)에 일별 손절 적용\n")

    print(f"  {'손절':>6} {'CAGR':>8} {'MDD':>8} {'Sharpe':>7} {'Calmar':>7} {'최악의해':>9} "
          f"{'발동/년':>8} {'헛손절':>8}")
    print("  " + "-" * 70)
    res = {}
    for stop in STOPS:
        eq, hit, rec = run(P, S, n_days, n_sym, stop, track=True)
        s = stats(eq, idx)
        res[stop] = s
        lab = "없음" if stop is None else f"{stop:.0f}%"
        rate = f"{hit / s['yrs']:.1f}" if stop is not None else "–"
        whip = f"{rec / hit * 100:.0f}%" if hit else "–"
        print(f"  {lab:>6} {s['cagr']*100:>7.1f}% {s['mdd']*100:>7.1f}% {s['sharpe']:>7.2f} "
              f"{s['calmar']:>7.2f} {s['worst_yr']*100:>8.1f}% {rate:>8} {whip:>8}")
    print(f"  ※ 헛손절 = 털린 뒤 {RECOVER_DAYS}거래일 안에 5%+ 반등한 비율 (높을수록 조기 이탈)")

    cur = res[-8.0]
    print(f"\n  === 현행 -8% 대비 ===")
    for stop in STOPS:
        if stop == -8.0:
            continue
        s = res[stop]
        lab = "손절 없음" if stop is None else f"{stop:.0f}%"
        print(f"  {lab:>9}  CAGR {(s['cagr']-cur['cagr'])*100:+6.1f}%p · "
              f"MDD {(abs(cur['mdd'])-abs(s['mdd']))*100:+6.1f}%p · "
              f"Calmar {s['calmar']-cur['calmar']:+.2f}")

    # ── 강건성: 리밸런스 위상 (레짐 필터를 뒤집은 그 테스트) ──────────────
    cands = sorted(res, key=lambda k: res[k]["calmar"], reverse=True)[:3]
    print(f"\n  === 강건성: 리밸런스 위상 {REBAL}가지 ===")
    print(f"  {'손절':>6} {'Calmar 평균':>12} {'범위':>16} {'-8% 대비 승':>12}")
    print("  " + "-" * 50)
    phase_cal = {}
    for stop in sorted(set(cands + [-8.0]), key=lambda x: (x is not None, x)):
        cals = [stats(run(P, S, n_days, n_sym, stop, phase=ph)[0], idx)["calmar"]
                for ph in range(REBAL)]
        phase_cal[stop] = cals
    base = phase_cal[-8.0]
    for stop, cals in phase_cal.items():
        lab = "없음" if stop is None else f"{stop:.0f}%"
        w = sum(a > b for a, b in zip(cals, base))
        mark = "" if stop == -8.0 else f"{w}/{REBAL}"
        print(f"  {lab:>6} {np.mean(cals):>12.2f} {min(cals):>7.2f}~{max(cals):<8.2f} {mark:>12}")

    # ── 손절 후 실제로 어떻게 됐나 (손절이 살렸나 / 헛손절이었나) ──────────
    print("\n  === -8% 손절 288건, 그 뒤 21거래일 ===")
    fwd = post_stop_returns(P, S, n_days, -8.0)
    bands = ((-1e9, -20, "추가 -20% 이하 (크게 살림)"), (-20, -5, "추가 -20~-5% (살림)"),
             (-5, 5, "±5% 횡보 (무의미)"), (5, 20, "+5~20% 반등 (헛손절)"),
             (20, 1e9, "+20% 이상 반등 (큰 헛손절)"))
    for lo, hi, lab in bands:
        n = int(((fwd > lo) & (fwd <= hi)).sum())
        print(f"  {lab:<28} {n:>4}건 ({n / len(fwd) * 100:>4.1f}%)")
    print(f"  중앙값 {np.median(fwd):+.1f}% · 평균 {fwd.mean():+.1f}% — "
          f"{'손절 후 더 빠짐' if fwd.mean() < 0 else '**손절 후 반등**(자른 게 손해)'}")

    print("\n  === 판정 ===")
    best = max(phase_cal, key=lambda k: np.mean(phase_cal[k]))
    wins = sum(a > b for a, b in zip(phase_cal[best], base))
    if best == -8.0:
        print("  현행 -8% 유지 — 위상 평균 Calmar 최고. 바꿀 근거 없음")
    elif wins >= REBAL * 0.7:
        print(f"  {best:.0f}%로 변경 검토 — 위상 {wins}/{REBAL} 우세 (평균 Calmar "
              f"{np.mean(phase_cal[best]):.2f} vs {np.mean(base):.2f})")
    else:
        print(f"  {best:.0f}%가 평균은 높으나 위상 {wins}/{REBAL} — 표본 운. **현행 유지**")
    return res


if __name__ == "__main__":
    main()
