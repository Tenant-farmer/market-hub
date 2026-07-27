"""백테스트 장기 캐시 재생성 — data/us_px_cache.pkl (2015~ 현재).

검증 전용 캐시. 라이브 캐시(us_px_live.pkl)와 별개 파일이며 서로 덮어쓰지 않는다.
2026-07-27 사고(라이브 400일 캐시가 11년 검증 캐시를 덮어씀) 복구용으로 만들어 상시 보관.

실행: python scripts/rebuild_bt_cache.py   (약 3~5분, yfinance 503종목)
"""
import pickle
import sys
from pathlib import Path

import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")
from src import db  # noqa: E402

OUT = ROOT / "data" / "us_px_cache.pkl"
START = "2015-01-01"


def main():
    con = db.connect()
    syms = sorted(r["stock_code"] for r in con.execute(
        "SELECT DISTINCT stock_code FROM sector_map WHERE market='US_STOCK'"))
    con.close()
    print(f"{len(syms)}종목 {START}~ 수집 중...", flush=True)
    frames = []
    for i in range(0, len(syms), 100):
        d = yf.download(syms[i:i + 100], start=START, auto_adjust=True, progress=False,
                        group_by="column", threads=True)["Close"]
        frames.append(d if isinstance(d, pd.DataFrame) else d.to_frame(syms[i]))
        print(f"  {min(i + 100, len(syms))}/{len(syms)}", flush=True)
    px = pd.concat(frames, axis=1).ffill()
    spy = yf.download("SPY", start=START, auto_adjust=True, progress=False)["Close"]
    spy = spy["SPY"] if isinstance(spy, pd.DataFrame) else spy
    px = px.loc[:, px.notna().sum() >= 300]
    OUT.write_bytes(pickle.dumps((px, spy)))
    print(f"완료: {px.shape[1]}종목 {px.index[0].date()}~{px.index[-1].date()} → {OUT}")


if __name__ == "__main__":
    main()
