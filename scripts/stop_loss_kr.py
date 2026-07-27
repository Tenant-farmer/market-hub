"""손절폭 검증 — 국내(KR) + 생존편향 영향 측정.

두 가지 질문에 답한다(2026-07-27 사용자 제기):
  ① 국장과 미장에 같은 손절폭을 쓰는 게 맞나?
  ② US 검증은 '현재 S&P500' 유니버스라 사라진 종목이 0개였다. 소멸 종목을 넣으면 결론이 바뀌나?

①의 사전 관찰: KR(시총 3천억↑) 연율변동성 중앙 64.3% vs US 29.5% — **2.2배**.
하루 -8% 이상 하락 빈도도 2.41% vs 0.45%로 **5.4배**. 같은 -8%가 전혀 다른 의미다.

②의 방법: 우리 DB엔 데이터가 끊긴 KR 종목 75개가 있고, 그중 19개(25.3%)가 고점 대비
-70% 이하로 사라졌다(최악 -98.1%). 이들을 **포함한 유니버스**와 **제외한 유니버스**를
같은 하네스로 돌려 최적 손절폭이 이동하는지 본다. 이동하면 생존편향이 결론을 왜곡한 것이고,
안 하면 US 결론을 KR에도 (변동성 조정 후) 적용할 수 있다.

상장폐지 처리: 마지막 가격에 청산(중립 가정). 소멸 전 급락은 데이터에 그대로 남아 있어
'손절이 그걸 잘랐는가'는 정확히 측정된다. 정리매매 잔여가치까지 0으로 보는 건 과대추정이라 안 함.

실행: python scripts/stop_loss_kr.py
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()
from src import db  # noqa: E402
from stop_loss_sweep import REBAL, TOPK, WARM, run, stats  # noqa: E402

STOPS = [None, -8.0, -15.0, -20.0, -25.0, -30.0]
PHASES = 7          # KR 유니버스가 커서 7위상 (등간격) — 21은 시간이 과함


def load_kr(include_gone: bool):
    """KR 일별 종가 피벗. include_gone=False면 2026-07 이후 데이터가 있는 종목만(생존자)."""
    con = db.connect()
    df = pd.read_sql_query(
        "SELECT symbol, date, close FROM prices_daily WHERE market='KR' AND close > 0", con)
    con.close()
    px = df.pivot(index="date", columns="symbol", values="close").sort_index()
    px.index = pd.to_datetime(px.index)
    last = px.apply(lambda c: c.last_valid_index())
    if not include_gone:
        px = px.loc[:, last >= pd.Timestamp("2026-07-01")]
    return px.ffill(limit=5)


def prep_kr(px, spy):
    ret63 = px / px.shift(63) - 1
    s63 = spy / spy.shift(63) - 1
    score = ret63.sub(s63.reindex(px.index), axis=0).where(px > px.rolling(50).mean())
    return px.to_numpy(), score.to_numpy()


def sweep(label, px, spy):
    P, S = prep_kr(px, spy)
    n_days = P.shape[0]
    print(f"\n=== {label} ({px.shape[1]}종목 · {px.index[0].date()}~{px.index[-1].date()}) ===")
    print(f"  {'손절':>6} {'CAGR':>8} {'MDD':>8} {'Calmar':>7} {'최악의해':>9} {'발동/년':>8}")
    print("  " + "-" * 52)
    out = {}
    for stop in STOPS:
        eq, hit, _ = run(P, S, n_days, P.shape[1], stop)
        s = stats(eq, px.index)
        out[stop] = s
        lab = "없음" if stop is None else f"{stop:.0f}%"
        rate = f"{hit / s['yrs']:.1f}" if stop is not None else "–"
        print(f"  {lab:>6} {s['cagr']*100:>7.1f}% {s['mdd']*100:>7.1f}% {s['calmar']:>7.2f} "
              f"{s['worst_yr']*100:>8.1f}% {rate:>8}")

    step = max(1, REBAL // PHASES)
    ph = {}
    for stop in STOPS:
        ph[stop] = [stats(run(P, S, n_days, P.shape[1], stop, phase=p)[0], px.index)["calmar"]
                    for p in range(0, REBAL, step)]
    best = max(ph, key=lambda k: np.mean(ph[k]))
    print(f"  위상 {len(ph[STOPS[0]])}가지 평균 Calmar: "
          + " · ".join(f"{'없음' if k is None else f'{k:.0f}%'} {np.mean(v):.2f}"
                       for k, v in ph.items()))
    print(f"  → 최적: {'손절 없음' if best is None else f'{best:.0f}%'}")
    return best, ph


def main():
    con = db.connect()
    spy = pd.read_sql_query(
        "SELECT date, close FROM prices_daily WHERE symbol='1001' ORDER BY date",
        con, index_col="date")["close"]                 # KR 벤치마크 = KOSPI
    con.close()
    spy.index = pd.to_datetime(spy.index)

    surv = load_kr(include_gone=False)
    full = load_kr(include_gone=True)
    print(f"생존자 유니버스 {surv.shape[1]}종목 / 전체(소멸 포함) {full.shape[1]}종목 "
          f"— 차이 {full.shape[1] - surv.shape[1]}종목")

    b1, _ = sweep("① 생존자만 (US 검증과 같은 편향)", surv, spy)
    b2, _ = sweep("② 소멸 종목 포함 (편향 없음)", full, spy)

    print("\n=== 판정 ===")
    print(f"  생존자만 최적 {'없음' if b1 is None else f'{b1:.0f}%'} · "
          f"소멸 포함 최적 {'없음' if b2 is None else f'{b2:.0f}%'}")
    if b1 == b2:
        print("  → 최적 손절폭이 이동하지 않음. **생존편향이 결론을 바꾸지 않는다**")
    else:
        print("  → 최적이 이동. **생존편향이 결론을 왜곡했다** — 소멸 포함 결과를 채택할 것")


if __name__ == "__main__":
    main()
