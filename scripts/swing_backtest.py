"""단타(스윙 눌림목) 백테스트 — 주도주 유니버스 대상.

가설: 상승추세 주도주가 단기 되돌림(눌림목)했을 때 사서 반등에 판다.
- 유니버스(매일 재선정): 63일 시장(SPY) 대비 초과수익 상위 → '주도주'
- 진입: 종가 > MA50 (상승추세 유지) AND RSI(14) < RSI_TH (과매도 되돌림)
        AND 종가 ≥ MA20 × (1 - DIP) 이내 (추세선 근처 눌림 — 붕괴 아님)
        → 신호 다음날 종가 체결 (룩어헤드 방지)
- 청산: +TP% 익절 / -SL% 손절 / 종가<MA50 추세이탈 / HOLD일 경과 (다음날 종가)
- 벤치마크: SPY 단순보유. 단타는 비용 민감 → 왕복 COST 반영

1단계(트레이드 통계): 모든 (종목,진입일) 트레이드를 독립 나열 → 승률·기대값으로 규칙 스크리닝.
2단계는 별도 스크립트(자본 K개 제약 곡선). 실행: python scripts/swing_backtest.py
"""
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import db  # noqa: E402

START = "2015-01-01"
COST = 0.0010                       # 왕복 거래비용(슬리피지+수수료) 10bp
LEAD_PCTL = 0.70                    # 주도주 = rs63 상위 30%
CACHE = Path(__file__).resolve().parents[1] / "data" / "us_px_cache.pkl"


def load():
    # DB의 US 개별종목은 이력이 짧음(수집 최근분만) → yfinance로 S&P500 현 구성 직접 다운로드
    # (⚠ 생존편향: 현 구성만 있어 수익 과대 — 판정은 SPY 초과+규칙 우열 비교로)
    con = db.connect()
    syms = sorted(r["stock_code"] for r in con.execute(
        "SELECT DISTINCT stock_code FROM sector_map WHERE market='US_STOCK'"))
    con.close()
    if CACHE.exists():
        px, spy = pickle.loads(CACHE.read_bytes())
        return px, spy
    frames = []
    for i in range(0, len(syms), 100):
        d = yf.download(syms[i:i + 100], start=START, auto_adjust=True, progress=False,
                        group_by="column", threads=True)["Close"]
        frames.append(d if isinstance(d, pd.DataFrame) else d.to_frame(syms[i]))
        print(f"  다운로드 {min(i + 100, len(syms))}/{len(syms)}")
    px = pd.concat(frames, axis=1).ffill()
    spy = yf.download("SPY", start=START, auto_adjust=True, progress=False)["Close"]
    spy = spy["SPY"] if isinstance(spy, pd.DataFrame) else spy
    px = px.loc[:, px.notna().sum() >= 250]
    CACHE.write_bytes(pickle.dumps((px, spy)))
    return px, spy


def _rsi(s: pd.Series, n=14):
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def build_signals(px, spy):
    """종목별 지표 → DataFrame 딕셔너리. 룩어헤드 없이 당일 종가까지만."""
    spy63 = spy / spy.shift(63) - 1
    ind = {}
    for sym in px.columns:
        c = px[sym].dropna()
        if len(c) < 120:
            continue
        ma20 = c.rolling(20).mean()
        ma50 = c.rolling(50).mean()
        rsi = _rsi(c)
        ret63 = c / c.shift(63) - 1
        rs63 = ret63 - spy63.reindex(c.index)
        ind[sym] = pd.DataFrame({"close": c, "ma20": ma20, "ma50": ma50,
                                 "rsi": rsi, "rs63": rs63})
    return ind


def _lead_threshold(ind, dates):
    """각 날짜의 rs63 70분위 (주도주 컷) 시계열."""
    wide = pd.DataFrame({s: d["rs63"] for s, d in ind.items()})
    return wide.quantile(LEAD_PCTL, axis=1)


def trades(ind, lead_th, rsi_th, dip, tp, sl, hold):
    """규칙으로 모든 트레이드 나열 → 각 트레이드 수익률(비용 후)."""
    out = []
    for sym, d in ind.items():
        c, ma20, ma50, rsi, rs63 = (d["close"].values, d["ma20"].values,
                                    d["ma50"].values, d["rsi"].values, d["rs63"].values)
        idx = d.index
        th = lead_th.reindex(idx).values
        i = 55
        n = len(c)
        while i < n - 1:
            lead = rs63[i] is not None and not np.isnan(rs63[i]) and rs63[i] >= th[i]
            trend = c[i] > ma50[i]
            dip_ok = (rsi[i] < rsi_th) and (c[i] >= ma20[i] * (1 - dip))
            if lead and trend and dip_ok:
                entry = c[i + 1]                       # 다음날 종가 체결
                j, exit_px = i + 1, None
                while j < n - 1 and j - (i + 1) < hold:
                    r = c[j] / entry - 1
                    if r >= tp or r <= -sl or c[j] < ma50[j]:
                        exit_px = c[j + 1]              # 조건 충족 → 다음날 종가 청산
                        break
                    j += 1
                if exit_px is None:
                    exit_px = c[min(j + 1, n - 1)]
                ret = (exit_px / entry - 1) - COST
                out.append((sym, idx[i + 1], j - i, ret))
                i = j + 1                              # 청산 후 재진입 가능
            else:
                i += 1
    return out


def summarize(tr, label):
    if not tr:
        print(f"{label:34} 트레이드 없음")
        return None
    rets = np.array([t[3] for t in tr])
    hold = np.mean([t[2] for t in tr])
    win = (rets > 0).mean()
    exp = rets.mean()
    # 기대값/트레이드 × 연간 회전(대략) — 규칙 우열용 근사 스코어
    print(f"{label:34}{len(tr):>6}{win:>7.0%}{exp * 100:>+9.2f}%{np.median(rets) * 100:>+9.2f}%"
          f"{hold:>7.1f}일{rets.std() * 100:>8.2f}%{(exp / rets.std()) if rets.std() else 0:>8.3f}")
    return {"n": len(tr), "win": win, "exp": exp, "sharpe_t": exp / rets.std() if rets.std() else 0}


def main():
    print("데이터 로드...")
    px, spy = load()
    print(f"유니버스 {px.shape[1]}종목 · {px.index[0].date()}~{px.index[-1].date()}")
    # 벤치마크: SPY B&H
    bh = spy.iloc[-1] / spy.iloc[0] - 1
    yrs = (spy.index[-1] - spy.index[0]).days / 365.25
    print(f"SPY 단순보유: {bh:+.0%} (CAGR {(1 + bh) ** (1 / yrs) - 1:+.1%})\n")
    ind = build_signals(px, spy)
    lead_th = _lead_threshold(ind, px.index)

    print(f"{'규칙 (RSI<·dip·TP·SL·HOLD)':34}{'N':>6}{'승률':>7}{'기대값':>9}{'중앙':>9}"
          f"{'보유':>7}{'변동':>8}{'스코어':>8}")
    best, best_key = None, None
    for rsi_th in (35, 40, 45):
        for dip in (0.03, 0.06):
            for tp, sl in ((0.06, 0.05), (0.10, 0.06), (0.15, 0.08)):
                for hold in (10, 20):
                    tr = trades(ind, lead_th, rsi_th, dip, tp, sl, hold)
                    lbl = f"RSI<{rsi_th} dip{dip:.0%} TP{tp:.0%} SL{sl:.0%} H{hold}"
                    s = summarize(tr, lbl)
                    if s and s["n"] >= 100 and (best is None or s["exp"] > best["exp"]):
                        best, best_key = s, lbl
    if best:
        print(f"\n최고 기대값 규칙: {best_key} → 승률 {best['win']:.0%}, "
              f"기대값/트레이드 {best['exp'] * 100:+.2f}% (비용 후)")
        print("주의: 트레이드 기대값이지 자본곡선 아님 — 양수 기대값+충분 표본이면 2단계(자본 K개 곡선)로")


if __name__ == "__main__":
    main()
