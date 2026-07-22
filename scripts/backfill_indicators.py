"""지표 탭용 히스토리 백필 — 대표지수/공포지표를 장기(2015~)로 확장.

기존 수집기는 최근만 보유(VVIX 1.7년·F&G 6일)라 시계열 분석 탭이 얕음. yfinance(auto_adjust=True로
수집기와 동일)로 SPY·QQQ·^VIX·^VVIX·KOSPI(1001)·KOSDAQ(2001)를, CNN 엔드포인트로 F&G를 과거로
백필. 기존 날짜는 건드리지 않고 '없는 날짜'만 삽입(최근분은 수집기가 계속 갱신). 재실행 안전(멱등). 1회성.

실행: python scripts/backfill_indicators.py
"""
import sys

import pandas as pd
import requests
import yfinance as yf

sys.path.insert(0, r"C:\Users\user\Desktop\github\market-hub")
from src import db

START = "2015-01-01"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
MAP = [  # (yfinance 심볼, 저장 symbol, market)
    ("SPY", "SPY", "US"), ("QQQ", "QQQ", "US"),
    ("^VIX", "^VIX", "US_INDEX"), ("^VVIX", "^VVIX", "MACRO"),
    ("^KS11", "1001", "KR_INDEX"), ("^KQ11", "2001", "KR_INDEX"),
]


def _f(x):
    return None if x is None or pd.isna(x) else float(x)


def backfill_prices(con):
    data = yf.download([m[0] for m in MAP], start=START, auto_adjust=True, progress=False)
    total = 0
    for yfsym, sym, market in MAP:
        try:
            df = data.xs(yfsym, axis=1, level=1)
        except KeyError:
            print(f"  {yfsym}: 다운로드 실패, 건너뜀")
            continue
        have = {r["date"] for r in con.execute(
            "SELECT date FROM prices_daily WHERE symbol=? AND market=?", (sym, market))}
        rows = []
        for ts, r in df.iterrows():
            d = ts.strftime("%Y-%m-%d")
            if d in have or pd.isna(r.get("Close")):
                continue
            rows.append((sym, market, d, _f(r.get("Open")), _f(r.get("High")),
                         _f(r.get("Low")), float(r["Close"]), _f(r.get("Volume")) or 0, None))
        con.executemany(
            "INSERT INTO prices_daily(symbol,market,date,open,high,low,close,volume,value) "
            "VALUES (?,?,?,?,?,?,?,?,?)", rows)
        total += len(rows)
        print(f"  {sym:6} [{market:9}] +{len(rows):5}행 (기존 {len(have)})")
    con.commit()
    return total


def backfill_fng(con):
    hist = None
    for start in ("2015-01-01", "2018-01-01", "2020-01-01", "2020-09-01", "2021-01-01"):
        r = requests.get(
            f"https://production.dataviz.cnn.io/index/fearandgreed/graphdata/{start}",
            headers=UA, timeout=30)
        if r.ok:
            hist = r.json()["fear_and_greed_historical"]["data"]
            break
    if not hist:
        print("  F&G 엔드포인트 응답 없음 — 건너뜀")
        return 0
    ser = {}
    for d in hist:
        ser[pd.to_datetime(d["x"], unit="ms").strftime("%Y-%m-%d")] = round(d["y"], 1)
    have = {r["date"] for r in con.execute(
        "SELECT date FROM sentiment_daily WHERE metric='fear_greed'")}
    rows = [(day, "fear_greed", v) for day, v in ser.items() if day not in have]
    con.executemany("INSERT INTO sentiment_daily(date,metric,value) VALUES (?,?,?)", rows)
    con.commit()
    print(f"  F&G    +{len(rows):5}행 (기존 {len(have)}), 범위 {min(ser)}~{max(ser)}")
    return len(rows)


def main():
    con = db.connect()
    print("가격/지수 백필 (yfinance)...")
    backfill_prices(con)
    print("Fear&Greed 백필 (CNN)...")
    backfill_fng(con)
    con.close()
    print("완료")


if __name__ == "__main__":
    main()
