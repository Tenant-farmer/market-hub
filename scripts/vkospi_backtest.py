"""VKOSPI vs 글로벌 VIX — KR 매수신호 비교 백테스트.

질문: KR 진입 신호를 글로벌 VIX×VVIX 대신 VKOSPI(코스피200 변동성지수)로 바꾸면
      KR 특유의 '저점 지연 58일' 문제가 개선되는가?

신호 정의 (모두 KR 다음 세션 반영 = 1일 지연, 룩어헤드 방지):
  A. 기존     : VIX≥30 or (VIX≥20 & VVIX≥95)          — 글로벌 공포
  B. VK 절대  : VKOSPI ≥ T (T 스윕)                    — 시리즈가 진짜 변동성 레벨일 때만 유효
  C. VK 백분위: VKOSPI ≥ 롤링 1년 P95                  — 스케일 무관(지수화 안전)
  D. VK 스파이크: VKOSPI / 63일 중앙값 ≥ 1.5           — 스케일 무관
  E. 결합     : A and (C or D)                          — 글로벌+로컬 동시 공포
  +낙폭 변형  : & KOSPI ≤ 52주 고점 -5%                — '상승 과열 변동성'(2025~26 멜트업) 제외,
                                                        진짜 공포(고변동+낙폭)만 남김

평가: ①에피소드(15일 갭)별 +21/63d KOSPI 수익·저점까지 일수/깊이 ②H=63 보유 에쿼티.
기간 분리: 2010~2024(정상 국면)와 전체(멜트업 포함)를 따로 보고 — 멜트업이 통계를 지배하는 것 방지.
선행: python -m src.collectors.vkospi --backfill 완료 후 실행.
실행: python scripts/vkospi_backtest.py
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import db  # noqa: E402

COST = 0.0010
H = 63


def load():
    con = db.connect()
    vk = pd.Series({r["date"]: r["close"] for r in con.execute(
        "SELECT date, close FROM prices_daily WHERE symbol='VKOSPI' ORDER BY date")})
    con.close()
    vk.index = pd.to_datetime(vk.index)
    raw = yf.download(["^KS11", "^VIX", "^VVIX"], start="2009-06-01",
                      auto_adjust=True, progress=False)["Close"]
    df = raw.dropna(subset=["^KS11"]).copy()
    df["VK"] = vk.reindex(df.index).ffill()
    df["^VIX"] = df["^VIX"].ffill()
    df["^VVIX"] = df["^VVIX"].ffill()
    df = df.dropna(subset=["VK"])
    return df


def identity_check(df):
    """알려진 역사 국면과 대조 — 이 시리즈가 진짜 변동성 '레벨'인지 판별."""
    print("=== 시리즈 정체성 검증 ===")
    vk = df["VK"]
    print(f"기간 {df.index[0].date()}~{df.index[-1].date()}  "
          f"min {vk.min():.1f} / 중앙값 {vk.median():.1f} / max {vk.max():.1f}")
    for d, what, expect in [("2011-08-09", "유럽 재정위기", "~50"),
                            ("2017-06-01", "저변동 국면", "~11-13"),
                            ("2020-03-19", "COVID 패닉", "~69"),
                            ("2024-08-05", "블랙먼데이", "~40")]:
        try:
            v = vk.asof(pd.Timestamp(d))
            print(f"  {d} {what:12} 실측 {v:7.2f}  (실제 VKOSPI 기대 {expect})")
        except Exception:
            pass
    # 레벨 판정: COVID 근방 최대가 60~90이면 진짜 레벨
    covid = vk.loc["2020-02-15":"2020-04-15"].max() if len(vk.loc["2020-02-15":"2020-04-15"]) else np.nan
    level_like = 55 <= covid <= 95
    print(f"→ 판정: {'진짜 변동성 레벨' if level_like else '지수화/다른 시리즈 의심 — 절대 임계는 무효, 백분위·스파이크만 신뢰'}"
          f" (COVID 최대 {covid:.1f})\n")
    return level_like


def make_signals(df):
    v, w, vk = df["^VIX"].values, df["^VVIX"].values, df["VK"]
    sig = {"A 기존 VIX×VVIX": (v >= 30) | ((v >= 20) & (w >= 95))}
    for t in (30, 35, 40):
        sig[f"B VK≥{t}"] = (vk >= t).values
    p95 = vk.rolling(252, min_periods=200).quantile(0.95)
    sig["C VK≥롤링P95"] = (vk >= p95).fillna(False).values
    ratio = vk / vk.rolling(63, min_periods=40).median()
    sig["D VK스파이크1.5x"] = (ratio >= 1.5).fillna(False).values
    sig["E 결합 A&(C|D)"] = sig["A 기존 VIX×VVIX"] & (sig["C VK≥롤링P95"] | sig["D VK스파이크1.5x"])
    # 낙폭 조건: 52주 고점 대비 -5% 이하일 때만 — 멜트업 변동성(상승 과열)을 공포와 구분
    dd = (df["^KS11"] / df["^KS11"].rolling(252, min_periods=1).max() - 1).values <= -0.05
    sig["A+낙폭5%"] = sig["A 기존 VIX×VVIX"] & dd
    sig["B35+낙폭5%"] = sig["B VK≥35"] & dd
    sig["CD+낙폭5%"] = (sig["C VK≥롤링P95"] | sig["D VK스파이크1.5x"]) & dd
    # 전부 1일 지연 (다음 KR 세션 반영)
    return {k: np.concatenate([[False], s[:-1]]) for k, s in sig.items()}


def episodes(dates, px, green):
    """green 시작일(15일 갭) → +21/63d 수익, 이후 126d 내 저점 깊이/지연."""
    idx = np.where(green)[0]
    if not len(idx):
        return []
    starts = [idx[0]] + [j for i, j in zip(idx, idx[1:]) if j - i > 15]
    out = []
    for s in starts:
        if s + 1 >= len(px):
            continue
        p0 = px[s]
        r21 = px[s + 21] / p0 - 1 if s + 21 < len(px) else np.nan
        r63 = px[s + 63] / p0 - 1 if s + 63 < len(px) else np.nan
        w = px[s:s + 127]
        t = int(np.nanargmin(w))
        out.append({"date": dates[s], "r21": r21, "r63": r63,
                    "tr_d": t, "tr_pct": w[t] / p0 - 1})
    return out


def equity(px, green):
    eq, left, hold = 1.0, 0, False
    inv = 0
    for i in range(len(px)):
        if i > 0 and hold:
            eq *= px[i] / px[i - 1]
        if green[i]:
            left = H
        nh = left > 0
        if nh != hold:
            eq *= 1 - COST
        hold = nh
        inv += hold
        left = max(0, left - 1)
    return eq, inv / len(px)


def report(dates, px, sigs, level_like, lo=None, hi=None):
    mask = np.ones(len(dates), bool)
    if lo:
        mask &= dates >= pd.Timestamp(lo)
    if hi:
        mask &= dates <= pd.Timestamp(hi)
    d2, p2 = dates[mask], px[mask]
    bh = p2[-1] / p2[0] - 1
    yrs = (d2[-1] - d2[0]).days / 365.25
    print(f"단순보유: {bh:+.0%} (CAGR {(1 + bh) ** (1 / yrs) - 1:+.1%})")
    print(f"{'신호':18}{'에피':>4}{'green%':>7}{'+21d승':>7}{'+21d중앙':>9}{'+63d승':>7}"
          f"{'+63d중앙':>9}{'저점지연':>9}{'저점깊이':>9}{'에쿼티':>9}{'투자%':>6}")
    for name, g in sigs.items():
        if name.startswith("B") and not level_like:
            continue                        # 지수화 시리즈면 절대 임계 무의미
        g2 = g[mask]
        eps = episodes(d2, p2, g2)
        if not eps:
            print(f"{name:18}{0:>4}  (신호 없음)")
            continue
        r21 = [e["r21"] for e in eps if not np.isnan(e["r21"])]
        r63 = [e["r63"] for e in eps if not np.isnan(e["r63"])]
        eq, inv = equity(p2, g2)
        print(f"{name:18}{len(eps):>4}{g2.mean():>7.1%}"
              f"{np.mean([r > 0 for r in r21]) if r21 else float('nan'):>7.0%}"
              f"{np.median(r21) if r21 else float('nan'):>+9.1%}"
              f"{np.mean([r > 0 for r in r63]) if r63 else float('nan'):>7.0%}"
              f"{np.median(r63) if r63 else float('nan'):>+9.1%}"
              f"{np.median([e['tr_d'] for e in eps]):>8.0f}일"
              f"{np.median([e['tr_pct'] for e in eps]):>+9.1%}"
              f"{eq - 1:>+9.0%}{inv:>6.0%}")


def main():
    df = load()
    level_like = identity_check(df)
    dates, px = df.index, df["^KS11"].values
    sigs = make_signals(df)
    print(f"=== KOSPI 진입 비교 (H={H}일 보유, 편도 10bp, 1일 지연) ===")
    print("\n--- ① 2010 ~ 2024-12 (정상 국면 — 판단 기준) ---")
    report(dates, px, sigs, level_like, hi="2024-12-31")
    print("\n--- ② 전체 2010 ~ 현재 (2025~26 멜트업 포함 — 참고용) ---")
    report(dates, px, sigs, level_like)
    # 에피소드 목록 (기존 vs 후보 육안 대조용, 정상 국면)
    print("\n=== 에피소드 시작일 (2010~2024) ===")
    m = dates <= pd.Timestamp("2024-12-31")
    for name in ("A 기존 VIX×VVIX", "B VK≥30", "CD+낙폭5%"):
        if name in sigs:
            eps = episodes(dates[m], px[m], sigs[name][m])
            print(f"{name}: " + ", ".join(e["date"].strftime("%y-%m-%d") for e in eps))


if __name__ == "__main__":
    main()
