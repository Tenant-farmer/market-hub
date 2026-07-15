"""yfinance 배치 다운로드 공용부 (us_sectors / us_stocks 공유)."""
from typing import Callable

import yfinance as yf

CHUNK = 100

PRICE_COLS = ["symbol", "market", "date", "open", "high", "low", "close", "volume", "value"]


def fetch_daily_rows(symbols: list[str], days: int, market_for: Callable[[str], str]) -> list[tuple]:
    """prices_daily 형식의 row 목록 생성. market_for(sym) → market 컬럼 값."""
    rows = []
    for i in range(0, len(symbols), CHUNK):
        chunk = symbols[i:i + CHUNK]
        data = yf.download(
            chunk, period=f"{days}d", auto_adjust=True,
            group_by="ticker", progress=False, threads=True,
        )
        for sym in chunk:
            try:
                df = data if len(chunk) == 1 else data[sym]
                df = df.dropna(subset=["Close"])
            except KeyError:
                continue
            for dt, r in df.iterrows():
                rows.append((
                    sym, market_for(sym), dt.strftime("%Y-%m-%d"),
                    r["Open"], r["High"], r["Low"], r["Close"],
                    r["Volume"], r["Close"] * r["Volume"],
                ))
    return rows
