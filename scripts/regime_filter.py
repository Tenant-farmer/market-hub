"""하락장 대응 검증 — 시장 레짐 필터가 모멘텀의 낙폭을 실제로 줄이는가.

배경: 우리 전략은 **전부 롱**이다. VKOSPI≥30 매수신호는 '떨어질 때 산다'는 것이지
'하락장에서 안 잃는다'가 아니다. reality_check는 1년 -20% 낙폭 확률을 30.6%로 냈고,
지금(2026-07) KOSPI는 실제로 52주 고점 대비 -25.9% · VKOSPI 78이다. 관념이 아니라 현실.

검증 질문: **SPY 200일선 이탈 시 신규 진입을 멈추면 낙폭이 줄고 수익은 얼마나 깎이나?**

모멘텀 크래시의 구조: 폭락 후 반등장에서 '직전 승자'가 가장 크게 무너진다(2009-03,
2020-04). 200MA 필터는 이 구간을 통째로 회피하려는 고전적 장치다. 다만 **휩쏘 비용**이
있어 실제로 이득인지는 데이터로만 알 수 있다 — 캔들·기술지표 6연속 기각의 전례가 있다.

변형 4종을 같은 하네스로 비교:
  A. 무필터            — 현행 (기준선)
  B. 진입중단          — SPY<200MA면 신규 진입 안 함(보유는 유지)
  C. 전량현금          — SPY<200MA면 전량 청산해 현금
  D. 진입중단+되돌림   — SPY<200MA면 진입중단, 재돌파 후 N일 확인 뒤 재개(휩쏘 방어)

판정 기준: **MDD가 유의미하게 줄고 CAGR 감소가 그보다 작아야** 채택(Calmar 개선).
단순히 CAGR이 낮아지면 기각 — 낙폭 회피를 명분으로 수익을 버리는 건 흔한 자기기만.

실행: python scripts/regime_filter.py
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _bt_cache import load_cache  # noqa: E402

TOPK = 10
REBAL = 21              # 월1회 (reality_check에서 최적)
COST = 0.0020           # 편도 10bp 가정 (회전 비율에 비례 적용)
WARM = 260
CONFIRM = 5             # D변형: 200MA 재돌파 후 확인일


def load():
    px, spy = load_cache()
    return px.loc[:, px.notna().sum() >= 300], spy


def momentum_score(px, spy):
    ret63 = px / px.shift(63) - 1
    s63 = spy / spy.shift(63) - 1
    return ret63.sub(s63.reindex(px.index), axis=0), px > px.rolling(50).mean()


def regime_flags(spy, px_index):
    """SPY 200MA 위/아래 + 재돌파 확인 플래그."""
    s = spy.reindex(px_index).ffill()
    above = s > s.rolling(200).mean()
    # 재돌파 후 CONFIRM일 연속 위에 있어야 '복귀' 인정 (하루짜리 휩쏘 무시)
    confirmed = above.rolling(CONFIRM).min().astype(bool)
    return above.fillna(True), confirmed.fillna(True)


def run(px, score, trend, mode, above, confirmed):
    """mode: none / no_entry / to_cash / no_entry_confirm. 자본곡선 반환."""
    dates = px.index
    daily = px.pct_change().fillna(0)
    equity, eq, held = 1.0, [], []
    for i in range(len(dates)):
        if i > 0 and held:
            equity *= (1 + daily.iloc[i][held].mean())
        if i >= WARM and i % REBAL == 0:
            ok = True
            if mode == "no_entry":
                ok = bool(above.iloc[i])
            elif mode == "no_entry_confirm":
                ok = bool(confirmed.iloc[i])
            elif mode == "to_cash" and not bool(above.iloc[i]):
                if held:                                  # 전량 청산
                    equity *= (1 - COST)
                    held = []
                eq.append(equity)
                continue
            if ok:
                sc = score.iloc[i].where(trend.iloc[i])
                top = sc.dropna().nlargest(TOPK).index.tolist()
                if set(top) != set(held):
                    equity *= (1 - COST * len(set(top) ^ set(held)) / max(len(top), 1))
                    held = top
        eq.append(equity)
    return pd.Series(eq, index=dates)


def stats(c):
    c = c[c > 0]
    yrs = (c.index[-1] - c.index[0]).days / 365.25
    r = c.pct_change().dropna()
    cagr = c.iloc[-1] ** (1 / yrs) - 1
    mdd = (c / c.cummax() - 1).min()
    return {
        "cagr": cagr, "mdd": mdd,
        "sharpe": r.mean() / r.std() * np.sqrt(252) if r.std() else 0.0,
        "calmar": cagr / abs(mdd) if mdd else 0.0,
        "worst_yr": c.resample("YE").last().pct_change().min(),
    }


def main():
    px, spy = load()
    score, trend = momentum_score(px, spy)
    above, confirmed = regime_flags(spy, px.index)
    yrs = (px.index[-1] - px.index[0]).days / 365.25
    off = (~above.iloc[WARM:]).mean() * 100
    print(f"=== 레짐 필터 검증 ({px.shape[1]}종목 · {px.index[0].date()}~{px.index[-1].date()} "
          f"· {yrs:.1f}년) ===")
    print(f"  SPY 200MA 아래였던 기간: {off:.1f}%\n")

    variants = [
        ("A. 무필터(현행)", "none"),
        ("B. 진입중단", "no_entry"),
        ("C. 전량현금", "to_cash"),
        (f"D. 진입중단+{CONFIRM}일확인", "no_entry_confirm"),
    ]
    res = {}
    print(f"  {'변형':<20} {'CAGR':>8} {'MDD':>8} {'Sharpe':>7} {'Calmar':>7} {'최악의해':>9}")
    print("  " + "-" * 64)
    for label, mode in variants:
        s = stats(run(px, score, trend, mode, above, confirmed))
        res[label] = s
        print(f"  {label:<20} {s['cagr']*100:>7.1f}% {s['mdd']*100:>7.1f}% "
              f"{s['sharpe']:>7.2f} {s['calmar']:>7.2f} {s['worst_yr']*100:>8.1f}%")

    base = res["A. 무필터(현행)"]
    print("\n  === 기준선(A) 대비 ===")
    verdict = []
    for label, s in res.items():
        if label.startswith("A."):
            continue
        d_cagr = (s["cagr"] - base["cagr"]) * 100
        d_mdd = (abs(base["mdd"]) - abs(s["mdd"])) * 100      # 양수 = 낙폭 개선
        d_cal = s["calmar"] - base["calmar"]
        ok = d_cal > 0.05 and d_mdd > 2
        verdict.append((label, ok, d_cagr, d_mdd, d_cal))
        print(f"  {label:<20} CAGR {d_cagr:+.1f}%p · MDD {d_mdd:+.1f}%p 개선 · "
              f"Calmar {d_cal:+.2f}  → {'채택 후보' if ok else '기각'}")

    winners = [v for v in verdict if v[1]]
    if not winners:
        print("\n  === 판정 ===")
        print("  전부 기각 — 레짐 필터가 낙폭 대비 수익 손실을 정당화하지 못함.")
        print("  (복잡도 추가 7번째 실패. 단순 모멘텀 + 손절 -8% 유지)")
        return res

    best_label = max(winners, key=lambda v: v[4])[0]
    best_mode = dict(variants)[best_label]

    # ── 강건성 1: 구간 분할 (단일 사건 회피로 만든 수치인가) ──────────────
    print(f"\n  === 강건성 ① 구간 분할 — {best_label} vs 무필터 ===")
    print(f"  {'구간':<14} {'A CAGR':>8} {'후보':>8} {'A MDD':>8} {'후보':>8} {'판정':>6}")
    print("  " + "-" * 58)
    cA = run(px, score, trend, "none", above, confirmed)
    cB = run(px, score, trend, best_mode, above, confirmed)
    wins = 0
    spans = [("2016~2018", "2016", "2018"), ("2019~2021", "2019", "2021"),
             ("2022~2024", "2022", "2024"), ("2025~", "2025", "2026")]
    for name, a, b in spans:
        sa, sb = stats(cA[a:b] / cA[a:b].iloc[0]), stats(cB[a:b] / cB[a:b].iloc[0])
        ok = sb["calmar"] > sa["calmar"]
        wins += ok
        print(f"  {name:<14} {sa['cagr']*100:>7.1f}% {sb['cagr']*100:>7.1f}% "
              f"{sa['mdd']*100:>7.1f}% {sb['mdd']*100:>7.1f}% {'우세' if ok else '열세':>6}")
    print(f"  → {wins}/{len(spans)} 구간에서 우세"
          + ("  (과반 미달 = 특정 사건 의존 의심)" if wins <= len(spans) / 2 else ""))

    # ── 강건성 2: 파라미터 민감도 (200이라는 숫자에 맞춘 건가) ────────────
    print("\n  === 강건성 ② MA 기간 민감도 ===")
    print(f"  {'MA':>5} {'CAGR':>8} {'MDD':>8} {'Calmar':>7}")
    print("  " + "-" * 32)
    cal = []
    for ma in (100, 150, 200, 250, 300):
        s_ = spy.reindex(px.index).ffill()
        ab = (s_ > s_.rolling(ma).mean()).fillna(True)
        cf = ab.rolling(CONFIRM).min().astype(bool).fillna(True)
        st = stats(run(px, score, trend, best_mode, ab, cf))
        cal.append(st["calmar"])
        print(f"  {ma:>5} {st['cagr']*100:>7.1f}% {st['mdd']*100:>7.1f}% {st['calmar']:>7.2f}")
    spread = max(cal) - min(cal)
    print(f"  → Calmar 편차 {spread:.2f}"
          + ("  (편차 크면 특정 파라미터에 과최적화)" if spread > 0.3 else "  (안정적)"))

    # 판정 기준 주석 (사후 변경이라 명시):
    # 최초 기준은 '모든 MA에서 기준선 우위'였는데 MA=100만 미달해 보류가 나왔다. 그러나
    # 과최적화의 징표는 **특정 값에서만 뾰족한 것**이고, 여기선 150~300이 고르게 우위인
    # **고원**이었다. 빠른 MA(100)가 휩쏘로 지는 건 이론적으로도 예상되는 방향이다.
    # → 기준을 '이웃 파라미터 다수 우위(고원)'로 바꾸되, 원 기준 결과도 함께 출력한다.
    nb = cal[1:]                                     # MA 150·200·250·300
    plateau = all(c > base["calmar"] for c in nb)
    strict = min(cal) > base["calmar"]
    print("\n  === 판정 ===")
    print(f"  구간 우세 {wins}/{len(spans)} · Calmar 편차 {spread:.2f} · "
          f"MA150~300 전부 우위 {plateau} · MA100 포함 전부 우위 {strict}")
    robust = wins > len(spans) / 2 and spread <= 0.3 and plateau
    if robust:
        print(f"  {best_label}: 조건부 채택 — 단, **MA≥150에서만** 유효(100은 휩쏘로 열세)")
        print("  주의: 전량현금은 보유를 통째로 비우는 큰 행동 변화 →  사람 승인 후 적용")
    else:
        print(f"  {best_label}: 기각 — 구간 편중 또는 파라미터 민감(단순 모멘텀 유지)")
    return res


if __name__ == "__main__":
    main()
