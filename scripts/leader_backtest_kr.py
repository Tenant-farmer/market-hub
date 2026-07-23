"""KR 주도주 로테이션 백테스트 — yfinance(.KS/.KQ) 10년, 시총 3천억↑, KR 비용(30bp).

자체 DB의 KR 개별종목 이력은 ~252일(10년 커버 6종목뿐)이라 백테스트 불가 → US와 동일하게
yfinance로 장기 이력 확보(.KS 1차, 실패분 .KQ 2차). 규칙은 US와 동일: top10 진입/top30 이탈,
주1회, 편도 30bp(매도세 0.18%+수수료+슬리피지 근사). 유니버스 현재 시총 기준 → 생존편향 주의.
브레드스 가드: 유효 종목 300개 이상인 날부터 시뮬 시작(초기 얕은 구간의 밴드 왜곡 방지).

실행: python scripts/leader_backtest_kr.py
"""
import sys
from pathlib import Path

import pandas as pd
import yfinance as yf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import leader_backtest as lb
from src import db

CACHE = Path(__file__).resolve().parents[1] / "data" / "kr_px_cache.pkl"


def _grab(tickers):
    out = {}
    for i in range(0, len(tickers), 100):
        chunk = tickers[i:i + 100]
        d = yf.download(chunk, start="2015-01-01", auto_adjust=True,
                        progress=False, group_by="column")["Close"]
        if isinstance(d, pd.Series):
            d = d.to_frame(chunk[0])
        for c in d.columns:
            out[c] = d[c]
        print(f"  다운로드 {min(i + 100, len(tickers))}/{len(tickers)}")
    return pd.DataFrame(out)


def load_px(codes):
    if CACHE.exists():
        print("캐시 사용:", CACHE.name)
        return pd.read_pickle(CACHE)
    ks = _grab([f"{c}.KS" for c in codes])
    ks.columns = [c[:-3] for c in ks.columns]
    missing = [c for c in codes if c not in ks.columns or ks[c].notna().sum() < 100]
    print(f"  .KS 미확보 {len(missing)}종목 → .KQ 재시도")
    if missing:
        kq = _grab([f"{c}.KQ" for c in missing])
        kq.columns = [c[:-3] for c in kq.columns]
        for c in kq.columns:
            if kq[c].notna().sum() >= 100:
                ks[c] = kq[c]
    ks = ks.sort_index().ffill()
    ks.to_pickle(CACHE)
    return ks


def main():
    con = db.connect()
    codes = [r["symbol"] for r in con.execute(
        "SELECT s.symbol FROM stock_meta s JOIN sector_map m "
        "ON m.stock_code=s.symbol AND m.market='KR' WHERE s.mcap >= 3e11")]
    kospi = pd.read_sql_query(
        "SELECT date, close FROM prices_daily WHERE symbol='1001' ORDER BY date", con)
    con.close()
    print(f"KR 유니버스 {len(codes)}종목 (시총 3천억↑, 현재 기준 — 생존편향 주의)")

    px = load_px(codes)
    breadth = px.notna().sum(axis=1)
    start = breadth[breadth >= 300].index
    if len(start) == 0:
        sys.exit("유효 종목 300 미달 — 데이터 확인 필요")
    px = px.loc[start[0]:]
    dates, ret1d = px.index, px.pct_change()
    print(f"가격 {px.shape[1]}종목 × {len(dates)}일 ({dates[0].date()}~{dates[-1].date()}, "
          f"브레드스≥300 가드)\n")

    lb.COST = 0.0030
    rows = []
    ew = (1 + ret1d.mean(axis=1)).cumprod()
    rows.append(("유니버스 동일가중 보유", ew, 0, 0))
    ks = kospi.set_index("date")["close"]
    ks.index = pd.to_datetime(ks.index)
    ks = ks.loc[ks.index >= dates[0]]
    rows.append(("코스피 보유", ks / ks.iloc[0], 0, 0))
    for L, name in ((63, "63일(3개월) 로테이션"), (126, "126일(장기) 로테이션")):
        rank = (px / px.shift(L) - 1).rank(axis=1, ascending=False)
        eq, tpy, hold = lb.run(dates, ret1d, rank)
        rows.append((f"{name} 30bp", eq, tpy, hold))

    print(f"{'전략':26}{'총수익':>10}{'CAGR':>8}{'MaxDD':>8}{'샤프':>7}{'거래/년':>8}{'평균체류':>8}")
    for name, c, tpy, hold in rows:
        tr, cg, dd, sh = lb.stats(c.dropna())
        extra = f"{tpy:>7.0f} {hold:>6.0f}일" if tpy else f"{'—':>7} {'—':>7}"
        print(f"{name:26}{tr:>+10.0%}{cg:>+8.1%}{dd:>+8.1%}{sh:>7.2f}{extra}")


if __name__ == "__main__":
    main()
