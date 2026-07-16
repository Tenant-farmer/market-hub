"""섹터 거래대금 점유율 — "돈(관심)이 몰리는 곳"의 직접 측정.

각 섹터 구성종목의 거래대금 합이 시장 전체에서 차지하는 비중:
  val_share       = 최근 5일 평균 점유율(%)
  val_share_ratio = 최근 5일 점유율 / 최근 60일 평균 점유율 (1.0 = 평소 수준)

가격(RS)이 "결과"라면 이 지표는 "참여의 쏠림"이다 — 주체별 수급이 없는
미국 시장에서 자금 집중을 근사하는 프록시. (KR도 동일 계산 가능)
"""
import pandas as pd

from src import db
from src.analytics import store
from src.analytics.data import load_field

METRICS = ["val_share", "val_share_ratio"]


def compute(con, scope: str, stock_market: str, anchor_date: str | None = None) -> int:
    smap = {
        r["stock_code"]: r["sector_code"]
        for r in con.execute(
            "SELECT stock_code, sector_code FROM sector_map WHERE market=?", (stock_market,)
        )
    }
    if not smap:
        return 0
    stocks = sorted(smap)
    val = load_field(con, stocks, "value").tail(70)
    stocks = [s for s in stocks if s in val.columns]

    sec_of = pd.Series({s: smap[s] for s in stocks})
    daily_sec = val[stocks].T.groupby(sec_of).sum().T      # date × sector
    share = daily_sec.div(daily_sec.sum(axis=1), axis=0) * 100
    cur = share.tail(5).mean()
    base = share.tail(60).mean()

    # 랭킹 테이블과 같은 날짜로 저장해야 피벗 조회에서 합쳐진다
    date = anchor_date or val.index[-1]
    rows = []
    for sec in share.columns:
        if pd.isna(cur.get(sec)) or not base.get(sec):
            continue
        rows.append((date, scope, sec, "val_share", round(float(cur[sec]), 2)))
        rows.append((date, scope, sec, "val_share_ratio", round(float(cur[sec] / base[sec]), 2)))
    return store.replace_metrics(con, scope, METRICS, rows)
