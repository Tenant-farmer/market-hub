"""심리지표 수집: CNN Fear&Greed(비공식) + Cboe 풋콜비율 + VIX(수집분 재사용).

F&G와 Cboe는 비공식/외부 소스라 개별 실패를 허용한다 —
하나가 죽어도 나머지 지표는 적재되고, 대시보드는 해당 카드만 숨긴다.
"""
import csv
import io
from datetime import datetime

import requests

from src import db

FNG_URL = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
CBOE_PC = "https://cdn.cboe.com/resources/options/volume_and_call_put_ratios/equitypc.csv"
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
    # NOTE: 이 CSV는 2019년에 멈춘 레거시 아카이브로 확인됨 — 현행 소스 찾기 전까지
    # 최신 14일 이내 데이터만 수용 (스테일 값 적재 방지)
    r = requests.get(CBOE_PC, headers=UA, timeout=20)
    r.raise_for_status()
    last = None
    for row in csv.reader(io.StringIO(r.text)):
        if not row:
            continue
        try:
            dt = datetime.strptime(row[0].strip(), "%m/%d/%Y")
            ratio = float(row[-1])
        except ValueError:
            continue
        if (datetime.now() - dt).days <= 14:
            last = (dt.strftime("%Y-%m-%d"), "equity_pc_ratio", ratio)
    return [last] if last else []


def _vix_from_db(con) -> list[tuple]:
    row = con.execute(
        "SELECT date, close FROM prices_daily WHERE symbol='^VIX' ORDER BY date DESC LIMIT 1"
    ).fetchone()
    return [(row["date"], "vix", round(row["close"], 2))] if row else []
