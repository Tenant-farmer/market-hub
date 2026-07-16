"""KRX 업종지수 + 구성종목 수집 (pykrx).

⚠ KRX Data Marketplace 무료 계정 필요: .env에 KRX_ID / KRX_PW, pykrx>=1.2.8
과도 요청 시 IP 차단 위험 → 지수당 1초 딜레이.
수집 대상은 settings [kr].sector_codes (업종지수만) + 벤치마크.
"""
import time
from datetime import date, timedelta

from src import config, db
from src.collectors.krx_util import require_login
from src.collectors.yf_util import PRICE_COLS

MAP_COLS = ["stock_code", "market", "sector_code", "sector_name", "name", "as_of"]


def collect(con, days: int = 7) -> int:
    """업종지수 OHLCV → prices_daily(market='KR_INDEX') + 지수명 → sector_map."""
    require_login()
    from pykrx import stock

    cfg = config.load()["kr"]
    codes = [cfg["benchmark"]] + cfg.get("extra_indices", []) + cfg["sector_codes"]
    end = date.today().strftime("%Y%m%d")
    start = (date.today() - timedelta(days=days)).strftime("%Y%m%d")
    today = date.today().isoformat()

    price_rows, map_rows = [], []
    for code in codes:
        name = stock.get_index_ticker_name(code)
        df = stock.get_index_ohlcv(start, end, code)
        for dt, r in df.iterrows():
            price_rows.append((
                code, "KR_INDEX", dt.strftime("%Y-%m-%d"),
                float(r["시가"]), float(r["고가"]), float(r["저가"]), float(r["종가"]),
                float(r["거래량"]), float(r.get("거래대금", 0) or 0),
            ))
        # 지수 코드→이름 매핑 (대시보드 표시용)
        map_rows.append((code, "KR_INDEX", code, name, name, today))
        time.sleep(1)

    db.upsert(con, "sector_map", MAP_COLS, map_rows)
    return db.upsert(con, "prices_daily", PRICE_COLS, price_rows)


def refresh_constituents(con) -> int:
    """업종지수 구성종목 → sector_map(market='KR'). 주 1회면 충분."""
    require_login()
    from pykrx import stock

    cfg = config.load()["kr"]
    today = date.today().isoformat()
    rows = []
    for code in cfg["sector_codes"]:
        sector_name = stock.get_index_ticker_name(code)
        for tkr in stock.get_index_portfolio_deposit_file(code):
            rows.append((tkr, "KR", code, sector_name,
                         stock.get_market_ticker_name(tkr), today))
        time.sleep(1)
    return db.upsert(con, "sector_map", MAP_COLS, rows)
