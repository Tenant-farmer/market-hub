"""종목 로고 스캔 — 로고 유무와 밝기를 미리 재서 저장한다.

문제(2026-07-29 사용자 발견): 실적 캘린더에서 BA·RCL·REGN 등의 타일이 빈칸으로 보였다.
실측하니 **순백색 로고**(평균밝기 254~255)였다 — 다크 배경용으로 만들어진 것이라
흰 타일에서 사라진다. 게다가 투명 PNG가 뒤의 모노그램까지 덮어 폴백도 안 떴다.

배경을 흰색/어두운색 중 무엇으로 줄지는 **로고마다 다르다**(KO는 밝기 95라 흰 배경이 맞고
BA는 255라 어두운 배경이 필요). 브라우저에서 픽셀을 재는 건 CORS·비용 문제가 있으니
여기서 한 번 재서 DB에 넣고 렌더링은 그걸 읽어 쓴다.

- has_logo=0 이면 아예 img를 그리지 않는다 → 모노그램만 (투명 이미지가 덮는 문제 원천 차단)
- bright > 200 이면 어두운 타일, 아니면 흰 타일

실행: python scripts/logo_scan.py [--all]     (--all 없으면 미스캔 종목만)
"""
import io
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")
from src import db  # noqa: E402

URL = "https://financialmodelingprep.com/image-stock/{sym}.png"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
BRIGHT_DARK_BG = 200        # 이 밝기 초과면 어두운 타일이 필요


def ensure(con):
    con.execute("CREATE TABLE IF NOT EXISTS stock_logo ("
                "symbol TEXT PRIMARY KEY, has_logo INTEGER, bright REAL, scanned_at TEXT)")
    con.commit()


def _measure(sym: str):
    """(has_logo, 평균밝기). 밝기는 알파>20인 픽셀만 대상."""
    try:
        r = requests.get(URL.format(sym=sym), headers=UA, timeout=15)
    except Exception:
        return None                                   # 네트워크 실패 → 다음에 재시도
    if not r.ok or len(r.content) < 200:
        return 0, None                                # 404·빈 이미지 → 모노그램 사용
    try:
        from PIL import Image

        im = Image.open(io.BytesIO(r.content)).convert("RGBA")
        px = list(im.getdata())
        vis = [p for p in px if p[3] > 20]
        if not vis:
            return 0, None                            # 전부 투명 = 로고 없음과 같다
        return 1, sum((p[0] + p[1] + p[2]) / 3 for p in vis) / len(vis)
    except Exception:
        return 1, None                                # 파싱 실패해도 이미지는 있으니 흰 타일


def scan(con, symbols: list[str], sleep: float = 0.15) -> int:
    from datetime import datetime

    ensure(con)
    n = 0
    for i, s in enumerate(symbols, 1):
        m = _measure(s)
        if m is None:
            continue
        has, bright = m
        # **건별 커밋** — 50건씩 묶으면 쓰기 락을 15초씩 잡아 워커·hourly와 충돌한다
        # (2026-07-29: 450/496에서 'database is locked'로 스캔 자체가 죽었다)
        for attempt in range(3):
            try:
                con.execute("INSERT OR REPLACE INTO stock_logo VALUES (?,?,?,?)",
                            (s, has, bright, datetime.now().isoformat(timespec="seconds")))
                con.commit()
                break
            except Exception:
                time.sleep(2)
        n += 1
        if i % 50 == 0:
            print(f"  {i}/{len(symbols)} …")
        time.sleep(sleep)
    return n


def main():
    con = db.connect()
    ensure(con)
    universe = [r["stock_code"] for r in con.execute(
        "SELECT DISTINCT stock_code FROM sector_map WHERE market='US_STOCK' ORDER BY 1")]
    if "--all" not in sys.argv:
        done = {r["symbol"] for r in con.execute("SELECT symbol FROM stock_logo")}
        universe = [s for s in universe if s not in done]
    print(f"스캔 대상 {len(universe)}종목")
    n = scan(con, universe)
    rows = con.execute(
        "SELECT SUM(has_logo) y, COUNT(*) t, SUM(CASE WHEN bright > ? THEN 1 ELSE 0 END) d "
        "FROM stock_logo", (BRIGHT_DARK_BG,)).fetchone()
    print(f"완료 {n}건 · 전체 {rows['t']}종목 중 로고 있음 {rows['y']} · "
          f"어두운 타일 필요 {rows['d']}")
    con.close()


if __name__ == "__main__":
    main()
