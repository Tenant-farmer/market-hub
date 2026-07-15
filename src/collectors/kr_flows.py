"""KR 투자자별 순매수(수급) 수집 (pykrx).

시장 단위: 일별 외국인/기관/개인 순매수 대금 → investor_flows(scope='market')
종목 단위: 기간 내 외국인/기관 순매수 상위 N → investor_flows(scope='stock')
섹터 단위는 sector_map(구성종목) 롤업으로 분석 단계에서 계산.

⚠ KRX 계정 필요 — kr_sectors와 동일.
"""
import time
from datetime import date, timedelta

import pandas as pd

from src import config, db
from src.collectors.krx_util import require_login

FLOW_COLS = ["scope", "code", "date", "investor", "net_value", "net_volume"]

INVESTOR_MAP = {"외국인합계": "foreign", "기관합계": "institution", "개인": "individual"}


def collect(con, days: int = 7) -> int:
    require_login()
    from pykrx import stock

    top_n = config.load()["kr"]["flows_top_n"]
    end = date.today()
    start = end - timedelta(days=days)
    rows = []

    # 1) 시장 단위 일별 수급
    for mkt in ("KOSPI", "KOSDAQ"):
        d = start
        while d <= end:
            if d.weekday() < 5:
                ymd = d.strftime("%Y%m%d")
                try:
                    df = stock.get_market_trading_value_by_investor(ymd, ymd, mkt)
                except Exception:
                    df = pd.DataFrame()
                for inv_kr, inv in INVESTOR_MAP.items():
                    if inv_kr in df.index:
                        rows.append((
                            "market", mkt, d.isoformat(), inv,
                            float(df.loc[inv_kr, "순매수"]), None,
                        ))
                time.sleep(1)
            d += timedelta(days=1)

    # 2) 종목 단위: 기간 순매수 상위 N (외국인/기관)
    s_ymd, e_ymd = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
    for mkt in ("KOSPI", "KOSDAQ"):
        for inv_kr, inv in (("외국인", "foreign"), ("기관합계", "institution")):
            try:
                df = stock.get_market_net_purchases_of_equities(s_ymd, e_ymd, mkt, inv_kr)
            except Exception:
                continue
            df = df.sort_values("순매수거래대금", ascending=False).head(top_n)
            for tkr, r in df.iterrows():
                rows.append((
                    "stock", tkr, end.isoformat(), inv,
                    float(r["순매수거래대금"]), float(r.get("순매수거래량", 0) or 0),
                ))
            time.sleep(1)

    return db.upsert(con, "investor_flows", FLOW_COLS, rows)
