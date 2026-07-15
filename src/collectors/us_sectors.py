"""미국 섹터 ETF + 벤치마크 + VIX EOD 수집 (yfinance)."""
from src import config, db
from src.collectors.yf_util import PRICE_COLS, fetch_daily_rows


def collect(con, days: int = 7) -> int:
    cfg = config.load()["us"]
    symbols = cfg["symbols"] + [cfg["vix"]]
    rows = fetch_daily_rows(
        symbols, days,
        market_for=lambda s: "US_INDEX" if s.startswith("^") else "US",
    )
    return db.upsert(con, "prices_daily", PRICE_COLS, rows)
