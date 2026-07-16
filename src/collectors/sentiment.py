"""심리지표 수집: CNN Fear&Greed(비공식) + Cboe 풋콜비율 + VIX(수집분 재사용).

F&G와 Cboe는 비공식/외부 소스라 개별 실패를 허용한다 —
하나가 죽어도 나머지 지표는 적재되고, 대시보드는 해당 카드만 숨긴다.
"""
import io
from datetime import date, timedelta

import pandas as pd
import requests

from src import db

FNG_URL = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
CBOE_DAILY = "https://www.cboe.com/us/options/market_statistics/daily/"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

COLUMNS = ["date", "metric", "value"]


def collect(con) -> int:
    rows = []
    for name, fn in (("fear_greed", _fear_greed), ("put_call", _put_call)):
        try:
            rows += fn()
        except Exception as e:
            print(f"[sentiment] {name} skip: {e}")
    rows += _vix_from_db(con)
    return db.upsert(con, "sentiment_daily", COLUMNS, rows)


def _fear_greed() -> list[tuple]:
    r = requests.get(FNG_URL, headers=UA, timeout=20)
    r.raise_for_status()
    d = r.json()["fear_and_greed"]
    date = str(d["timestamp"])[:10]
    return [(date, "fear_greed", round(float(d["score"]), 1))]


def _put_call() -> list[tuple]:
    """Cboe 일별 통계 페이지(서버 렌더링)에서 EQUITY PUT/CALL RATIO 파싱.

    ?dt=로 최근 날짜부터 역순 시도 — 휴장/미마감일은 표가 비거나 0이라 건너뜀.
    (공식 무료 CSV 아카이브는 2019-10에 중단됨)
    """
    for back in range(1, 7):
        d = date.today() - timedelta(days=back)
        if d.weekday() >= 5:
            continue
        r = requests.get(CBOE_DAILY, params={"dt": d.isoformat()}, headers=UA, timeout=25)
        if not r.ok:
            continue
        try:
            tables = pd.read_html(io.StringIO(r.text))
        except ValueError:
            continue
        for t in tables:
            if "Ratios" not in t.columns:
                continue
            row = t[t["Ratios"].astype(str).str.contains("EQUITY PUT/CALL", na=False)]
            if len(row):
                val = float(row["Value"].iloc[0])
                if val > 0:
                    return [(d.isoformat(), "equity_pc_ratio", val)]
    return []


def _vix_from_db(con) -> list[tuple]:
    row = con.execute(
        "SELECT date, close FROM prices_daily WHERE symbol='^VIX' ORDER BY date DESC LIMIT 1"
    ).fetchone()
    return [(row["date"], "vix", round(row["close"], 2))] if row else []
