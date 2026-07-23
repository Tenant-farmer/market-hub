"""주도주 로테이션 백테스트 — '주도 순위 top10 매수, top30 이탈 시 매도' (신호 공백기 전략 검증).

사용자 제안: ① 주도 순위 상위 종목 매수 ② 주도 이탈 시 매도 ③ 단기·장기 렌즈 모두.
기각했던 섹터 로테이션/공포시점 픽과 다른, 개별종목 모멘텀 로테이션 + 이탈 청산.

- 유니버스: sector_map의 S&P500 현 구성 (⚠ **생존편향** — 상폐 패자 부재로 수익 과대평가.
  따라서 판정 기준은 SPY가 아니라 **같은 유니버스 동일가중 보유** 대비)
- 규칙: 주 1회(5거래일) 평가. 순위 top10 진입(동일가중 10슬롯) / top30 밖 이탈 시 매도(밴드=휩쏘 방지)
- 렌즈: 21일(단기) / 63일(3개월) / 126일(장기) 수익률 순위 + '63일+체류'(최근 15일 내내 top20 유지시만 진입)
- 편도 10bp, 룩어헤드 없음(당일 종가 순위→당일 종가 매매→익일 수익부터)

실행: python scripts/leader_backtest.py
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import db

START, COST, N_HOLD, ENTER_K, EXIT_K, EVAL_EVERY = "2015-01-01", 0.0010, 10, 10, 30, 5


def load_universe():
    con = db.connect()
    syms = [r["stock_code"] for r in con.execute(
        "SELECT DISTINCT stock_code FROM sector_map WHERE market='US_STOCK'")]
    con.close()
    return sorted(syms)


def download(symbols):
    frames = []
    for i in range(0, len(symbols), 100):
        chunk = symbols[i:i + 100]
        d = yf.download(chunk, start=START, auto_adjust=True, progress=False,
                        group_by="column", threads=True)["Close"]
        frames.append(d if isinstance(d, pd.DataFrame) else d.to_frame(chunk[0]))
        print(f"  다운로드 {min(i + 100, len(symbols))}/{len(symbols)}")
    px = pd.concat(frames, axis=1)
    bench = yf.download(["SPY", "QQQ"], start=START, auto_adjust=True, progress=False)["Close"]
    return px.ffill(), bench


def run(dates, ret1d, rank, elig=None):
    H, equity, eq, trades, inv_days = set(), 1.0, [], 0, 0
    warm = 260
    for i in range(len(dates)):
        if i > 0 and H:
            r = ret1d.iloc[i][list(H)]
            equity *= 1 + np.nanmean(r.values)
        if i >= warm and i % EVAL_EVERY == 0:
            row = rank.iloc[i]
            for s in list(H):                                  # 이탈 매도 (top EXIT_K 밖)
                if np.isnan(row.get(s, np.nan)) or row[s] > EXIT_K:
                    H.discard(s)
                    equity *= 1 - COST
                    trades += 1
            cands = row[row <= ENTER_K].dropna().sort_values().index
            for s in cands:                                     # top ENTER_K 진입
                if len(H) >= N_HOLD:
                    break
                if s in H:
                    continue
                if elig is not None and not bool(elig.iloc[i].get(s, False)):
                    continue
                H.add(s)
                equity *= 1 - COST
                trades += 1
        inv_days += len(H)
        eq.append(equity)
    yrs = (dates[-1] - dates[0]).days / 365.25
    return pd.Series(eq, index=dates), trades / yrs, inv_days / max(trades, 1)


def stats(c):
    r = c.pct_change().dropna()
    yrs = (c.index[-1] - c.index[0]).days / 365.25
    return (c.iloc[-1] - 1, c.iloc[-1] ** (1 / yrs) - 1,
            (c / c.cummax() - 1).min(), r.mean() / r.std() * np.sqrt(252) if r.std() else 0)


def main():
    syms = load_universe()
    print(f"유니버스 {len(syms)}종목 (S&P500 현 구성 — 생존편향 주의), 다운로드 중...")
    px, bench = download(syms)
    px = px.dropna(axis=1, how="all")
    dates = px.index
    ret1d = px.pct_change()
    print(f"가격 {px.shape[1]}종목 × {len(dates)}일 ({dates[0].date()}~{dates[-1].date()})\n")

    ranks = {}
    for L in (21, 63, 126):
        mom = px / px.shift(L) - 1
        ranks[L] = mom.rank(axis=1, ascending=False)           # 1 = 최고 주도

    # 체류 필터: 최근 15거래일 내내 top20 유지한 종목만 진입 가능 (이벤트 스터디 발견 반영)
    in20 = ranks[63] <= 20
    elig63 = in20.rolling(15).min().astype(bool)

    rows = []
    ew = (1 + ret1d.mean(axis=1)).cumprod()                    # 유니버스 동일가중 (편향 통제 기준)
    rows.append(("유니버스 동일가중 보유", ew, 0, 0))
    for b, name in (("SPY", "SPY 보유"), ("QQQ", "QQQ 보유")):
        c = bench[b].dropna()
        rows.append((name, c / c.iloc[0], 0, 0))
    for L, name in ((21, "① 단기 21일 로테이션"), (63, "② 3개월 63일 로테이션"),
                    (126, "③ 장기 126일 로테이션")):
        eq, tpy, hold = run(dates, ret1d, ranks[L])
        rows.append((name, eq, tpy, hold))
    eq, tpy, hold = run(dates, ret1d, ranks[63], elig63)
    rows.append(("④ 63일+체류15일 필터", eq, tpy, hold))

    print(f"{'전략':26}{'총수익':>10}{'CAGR':>8}{'MaxDD':>8}{'샤프':>7}{'거래/년':>8}{'평균체류':>8}")
    for name, c, tpy, hold in rows:
        tr, cg, dd, sh = stats(c)
        extra = f"{tpy:>7.0f} {hold:>6.0f}일" if tpy else f"{'—':>7} {'—':>7}"
        print(f"{name:26}{tr:>+10.0%}{cg:>+8.1%}{dd:>+8.1%}{sh:>7.2f}{extra}")


if __name__ == "__main__":
    main()
