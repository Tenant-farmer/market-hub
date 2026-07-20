"""KR 업종별 CapEx — KOSPI 비금융 업종의 시총 상위 종목 분기 현금흐름 합산.

야후(.KS)로 조회 (분기 5개 커버 확인: 삼전·하이닉스·코스닥 중형주까지).
금융 계열(금융·은행·증권·보험)은 CapEx 개념이 안 맞아 제외.
콜 수 관리를 위해 KOSPI 업종만, 업종당 상위 5종목 (~95콜).
"""
import time
from datetime import datetime

from src.collectors.us_capex import fetch_capex

SKIP_SECTORS = {"금융", "은행", "증권", "보험"}
TOP_N = 5


def collect(con, top_n: int = TOP_N) -> int:
    con.execute(
        "CREATE TABLE IF NOT EXISTS kr_capex ("
        "sector TEXT NOT NULL, symbol TEXT NOT NULL, latest_q TEXT, "
        "capex_ttm REAL, q_latest REAL, q_yoy_base REAL, fetched_at TEXT, "
        "PRIMARY KEY (sector, symbol))"
    )
    rows = con.execute(
        """
        SELECT m.sector_name AS sector, m.stock_code AS sym
        FROM sector_map m JOIN stock_meta s ON s.symbol = m.stock_code
        WHERE m.market='KR' AND m.sector_code LIKE '1%' AND s.mcap IS NOT NULL
        ORDER BY m.sector_name, s.mcap DESC
        """
    ).fetchall()
    by_sec: dict[str, list[str]] = {}
    for r in rows:
        if r["sector"] in SKIP_SECTORS:
            continue
        picks = by_sec.setdefault(r["sector"], [])
        if len(picks) < top_n:
            picks.append(r["sym"])

    fetched = datetime.now().isoformat(timespec="seconds")
    out = []
    for sector, syms in by_sec.items():
        for code in syms:
            try:
                got = fetch_capex(f"{code}.KS")
                if got:
                    out.append((sector, code, *got, fetched))
            except Exception:
                continue
            time.sleep(0.2)
    if out:
        con.execute("DELETE FROM kr_capex")
        con.executemany("INSERT OR REPLACE INTO kr_capex VALUES (?,?,?,?,?,?,?)", out)
        con.commit()
    return len(out)
