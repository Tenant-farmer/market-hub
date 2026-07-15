"""KR 투자자별 순매수(수급) 수집 (pykrx).

시장 단위: get_market_trading_value_by_date — 기간 전체를 한 번에 (일별×투자자)
종목 단위: 기간 내 외국인/기관 순매수 상위 N → investor_flows(scope='stock')
섹터 단위는 sector_map(구성종목) 롤업으로 분석 단계에서 계산.

⚠ KRX 계정 필요 — kr_sectors와 동일.
"""
import time
from datetime import date, timedelta

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
    s_ymd, e_ymd = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
    rows = []

    # 1) 시장 단위 일별 수급 — 기간 전체 단일 호출
    for mkt in ("KOSPI", "KOSDAQ"):
        tv = stock.get_market_trading_value_by_date(s_ymd, e_ymd, mkt)
        for dt, r in tv.iterrows():
            for inv_kr, inv in INVESTOR_MAP.items():
                if inv_kr in tv.columns:
                    rows.append((
                        "market", mkt, dt.strftime("%Y-%m-%d"), inv,
                        float(r[inv_kr]), None,
                    ))
        time.sleep(1)

    # 2) 종목 단위: 기간 순매수 상위 N (외국인/기관)
    name_cache: dict[str, str] = {}
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
                if "종목명" in df.columns:
                    name_cache[tkr] = str(r["종목명"])
            time.sleep(1)

    # 종목명 캐시: 업종 매핑에 없는 종목(KOSDAQ 등)의 표시용 이름.
    # sector_map PK가 stock_code라 기존 KOSPI 매핑을 덮지 않도록 INSERT OR IGNORE
    today = end.isoformat()
    con.executemany(
        "INSERT OR IGNORE INTO sector_map (stock_code, market, sector_code, sector_name, name, as_of) "
        "VALUES (?, 'KR_NAME', '', '', ?, ?)",
        [(t, n, today) for t, n in name_cache.items()],
    )
    con.commit()

    return db.upsert(con, "investor_flows", FLOW_COLS, rows)
