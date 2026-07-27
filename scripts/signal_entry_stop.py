"""신호진입(역발상 매수)에 손절을 거는 게 맞는가 — 자기모순 여부 검증.

문제 제기(2026-07-27): KR 신호진입은 **VKOSPI≥30 & KOSPI 낙폭 -5% 이하**일 때 KODEX200을
사는 전략이다. "공포에 산다"가 논거인데 여기에 손절을 걸면 **더 떨어지면 판다**가 되어
전략과 정면 충돌한다. 로테이션(추세추종)에는 손절이 맞지만 역발상 진입에는 아닐 수 있다.

검증: 2010~ KOSPI 일봉으로 green 신호일마다 진입 → 63거래일 보유(백테스트 원 설계)하되,
손절폭을 바꿔가며 실현 수익률을 비교한다. 지수는 개별 종목과 달리 0으로 가지 않으므로
'파산 회피'라는 손절의 본래 목적이 애초에 성립하지 않는다는 점도 함께 본다.

실행: python scripts/signal_entry_stop.py
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv  # noqa: E402

load_dotenv()
from src import db  # noqa: E402

HOLD = 63                    # 백테스트 원 설계 (승률 75% / 중앙 +5.3%)
STOPS = [None, -5.0, -8.0, -10.0, -15.0, -25.0]


def load():
    con = db.connect()
    ks = pd.read_sql_query(
        "SELECT date, close FROM prices_daily WHERE symbol='1001' ORDER BY date",
        con, index_col="date")["close"]
    vk = pd.read_sql_query(
        "SELECT date, close FROM prices_daily WHERE symbol='VKOSPI' ORDER BY date",
        con, index_col="date")["close"]
    con.close()
    df = pd.DataFrame({"ks": ks, "vk": vk}).dropna()
    dd = (df["ks"] / df["ks"].rolling(260, min_periods=200).max() - 1) * 100
    df["green"] = (df["vk"] >= 30) & (dd <= -5)
    return df


def simulate(df, stop):
    """green 신호일마다 진입 → HOLD 후 청산, 중간에 stop 이하면 조기 청산. 수익률 리스트."""
    px = df["ks"].to_numpy()
    idx = np.flatnonzero(df["green"].to_numpy())
    out = []
    for i in idx:
        end = min(i + HOLD, len(px) - 1)
        if end <= i:
            continue
        entry = px[i]
        r = (px[end] / entry - 1) * 100
        if stop is not None:
            path = (px[i + 1:end + 1] / entry - 1) * 100
            hit = np.flatnonzero(path <= stop)
            if len(hit):
                r = stop                       # 손절가 체결 가정 (갭 무시 — 낙관적)
        out.append(r)
    return np.array(out)


def main():
    df = load()
    n_green = int(df["green"].sum())
    print(f"=== 신호진입 손절 검증 (KOSPI {df.index[0]}~{df.index[-1]}) ===")
    print(f"  green 신호일 {n_green}일 / 전체 {len(df)}일 ({n_green / len(df) * 100:.1f}%)")
    print(f"  각 신호일 진입 → {HOLD}거래일 보유 (손절 시 조기 청산)\n")
    print(f"  {'손절':>6} {'평균':>8} {'중앙':>8} {'승률':>7} {'최악':>8} {'손절발동':>9}")
    print("  " + "-" * 50)
    res = {}
    for stop in STOPS:
        r = simulate(df, stop)
        if not len(r):
            continue
        res[stop] = r
        lab = "없음" if stop is None else f"{stop:.0f}%"
        hit = (r <= stop + 1e-9).mean() * 100 if stop is not None else 0.0
        print(f"  {lab:>6} {r.mean():>7.2f}% {np.median(r):>7.2f}% {(r > 0).mean()*100:>6.1f}% "
              f"{r.min():>7.2f}% {(f'{hit:.1f}%' if stop is not None else '–'):>9}")

    base = res[None]
    print(f"\n  === 손절 없음 대비 ===")
    for stop in STOPS[1:]:
        r = res[stop]
        print(f"  {stop:>4.0f}%  평균 {(r.mean()-base.mean()):+6.2f}%p · "
              f"승률 {((r>0).mean()-(base>0).mean())*100:+6.1f}%p")

    best = max(res, key=lambda k: res[k].mean())
    print("\n  === 판정 ===")
    if best is None:
        print("  **손절 없음이 최선** — 역발상 진입에 손절은 전략과 모순.")
        print("  지수는 0으로 가지 않으므로 '파산 회피'라는 손절 본래 목적도 성립하지 않는다.")
    else:
        print(f"  {best:.0f}%가 최선 — 역발상 진입에도 손절이 유효")
    return res


if __name__ == "__main__":
    main()
