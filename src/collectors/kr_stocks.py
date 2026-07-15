"""KR 개별종목 일별 시세 수집 (pykrx) — KR 주도주 스코어용.

get_market_ohlcv_by_ticker(날짜)로 하루 전체 시장을 1콜에 받는다 (시가총액 포함).
v1은 KOSPI만 — 업종 구성종목 매핑(sector_map)이 KOSPI 업종지수 기준이라
KOSDAQ 종목은 섹터 대비 RS를 계산할 수 없다. KOSDAQ은 이후 확장.

⚠ KRX 계정 필요. 하루 1콜 + 1초 딜레이 → 60일 백필 약 1~2분.
"""
import time
from datetime import date, timedelta

from src import db
from src.collectors.krx_util import require_login
from src.collectors.yf_util import PRICE_COLS

META_COLS = ["symbol", "mcap", "as_of"]


def collect(con, days: int = 7) -> int:
    require_login()
    from pykrx import stock

    end = date.today()
    start = end - timedelta(days=days)
    rows = []
    latest_mcap: dict[str, float] = {}
    d = start
    while d <= end:
        if d.weekday() < 5:
            ymd = d.strftime("%Y%m%d")
            df = stock.get_market_ohlcv_by_ticker(ymd, market="KOSPI")
            df = df[df["거래량"] > 0]  # 휴장일은 전 종목 0
            for tkr, r in df.iterrows():
                rows.append((
                    tkr, "KR", d.isoformat(),
                    float(r["시가"]), float(r["고가"]), float(r["저가"]), float(r["종가"]),
                    float(r["거래량"]), float(r.get("거래대금", 0) or 0),
                ))
                if "시가총액" in df.columns:
                    latest_mcap[tkr] = float(r["시가총액"])
            time.sleep(1)
        d += timedelta(days=1)

    if latest_mcap:
        as_of = end.isoformat()
        db.upsert(con, "stock_meta", META_COLS,
                  [(t, m, as_of) for t, m in latest_mcap.items()])
    return db.upsert(con, "prices_daily", PRICE_COLS, rows)
