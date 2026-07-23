"""VKOSPI 수집 — KRX Open API '파생상품지수 시세정보'(apis/idx/drvprod_dd_trd).

- KRX_OPENAPI_KEY 없으면 조용히 건너뜀 (키 게이트)
- 일자별 조회 API(basDd)라 하루 1요청 — 응답에서 변동성지수만 골라 저장
  (코스피 200 변동성지수 → VKOSPI, 코스닥 150 변동성지수 → VKOSDAQ)
- prices_daily(market='KR_INDEX')로 저장 → 지표탭·백테스트에서 기존 파이프 재사용
- 백필: python -m src.collectors.vkospi --backfill [2010-01-04부터, 이어받기 지원]
- 프로브: python -m src.collectors.vkospi --probe 20260722  (원본 JSON 확인용)
"""
import os
import time
from datetime import date, datetime, timedelta

import requests

from src import db
from src.collectors.yf_util import PRICE_COLS

URL = "https://data-dbg.krx.co.kr/svc/apis/idx/drvprod_dd_trd"
VOL_MAP = {"코스피 200 변동성지수": "VKOSPI", "코스닥 150 변동성지수": "VKOSDAQ"}
START = "2010-01-04"                       # API 제공 시작일


def _fetch(key: str, bas_dd: str) -> list[dict]:
    r = requests.get(URL, headers={"AUTH_KEY": key}, params={"basDd": bas_dd}, timeout=20)
    r.raise_for_status()
    return r.json().get("OutBlock_1") or []


def _num(v):
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _rows_for(key: str, d: date) -> list[tuple]:
    rows = []
    for it in _fetch(key, d.strftime("%Y%m%d")):
        name = (it.get("IDX_NM") or "").strip()
        sym = VOL_MAP.get(name) or ("VKOSPI" if "변동성" in name and "코스피" in name else None)
        if not sym:
            continue
        c = _num(it.get("CLSPRC_IDX"))
        if c is None:
            continue
        o, h, lo = (_num(it.get(k)) for k in ("OPNPRC_IDX", "HGPRC_IDX", "LWPRC_IDX"))
        rows.append((sym, "KR_INDEX", d.isoformat(),
                     o if o is not None else c, h if h is not None else c,
                     lo if lo is not None else c, c, 0, 0))
    return rows


def collect(con, days: int = 7) -> int:
    """최근 N일 갱신 (주말·휴장일은 빈 응답 → 자연 스킵)."""
    key = os.getenv("KRX_OPENAPI_KEY")
    if not key:
        return 0
    rows = []
    d = date.today()
    for _ in range(days):
        rows += _rows_for(key, d)
        d -= timedelta(days=1)
        time.sleep(0.3)
    return db.upsert(con, "prices_daily", PRICE_COLS, rows)


def backfill(con) -> int:
    """2010-01-04(또는 마지막 저장일 다음날)부터 오늘까지 전체 백필."""
    key = os.getenv("KRX_OPENAPI_KEY")
    if not key:
        print("KRX_OPENAPI_KEY 없음")
        return 0
    last = con.execute("SELECT MAX(date) d FROM prices_daily WHERE symbol='VKOSPI'").fetchone()["d"]
    d = (datetime.strptime(last, "%Y-%m-%d").date() + timedelta(days=1)) if last else \
        datetime.strptime(START, "%Y-%m-%d").date()
    today, n, calls = date.today(), 0, 0
    while d <= today:
        if d.weekday() < 5:                # 주말 스킵 (휴장일은 빈 응답)
            rows = _rows_for(key, d)
            n += db.upsert(con, "prices_daily", PRICE_COLS, rows)
            calls += 1
            if calls % 100 == 0:
                con.commit()
                print(f"  {d} 까지 {n}행 ({calls}요청)")
            time.sleep(0.25)
        d += timedelta(days=1)
    con.commit()
    print(f"백필 완료: {n}행 ({calls}요청)")
    return n


if __name__ == "__main__":
    import json
    import sys

    from dotenv import load_dotenv

    load_dotenv()
    sys.path.insert(0, ".")

    if "--probe" in sys.argv:
        bas = sys.argv[sys.argv.index("--probe") + 1]
        out = _fetch(os.getenv("KRX_OPENAPI_KEY"), bas)
        print(f"{len(out)}개 지수:")
        for it in out:
            print(" ", it.get("IDX_NM"), "=", it.get("CLSPRC_IDX"))
        if out:
            print("\n필드 샘플:", json.dumps(out[0], ensure_ascii=False, indent=1))
    else:
        c = db.connect()
        if "--backfill" in sys.argv:
            backfill(c)
        else:
            print("적재:", collect(c))
        r = c.execute("SELECT date, close FROM prices_daily WHERE symbol='VKOSPI' "
                      "ORDER BY date DESC LIMIT 3").fetchall()
        for x in r:
            print(f"  VKOSPI {x['date']} = {x['close']}")
        c.close()
