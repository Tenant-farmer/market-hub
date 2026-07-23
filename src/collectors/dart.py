"""DART 공시 수집 — 보유·로테이션 KR 종목의 최근 공시를 뉴스 스트림(news 테이블)에 통합.

- DART_API_KEY 없으면 조용히 건너뜀 (키 게이트)
- corp_code 매핑(8자리 내부코드)은 최초 1회 corpCode.xml(zip) 다운로드 → dart_corp 테이블
- 감시 대상: 고정 보유(삼성전자·S-Oil) + rotation_slots의 KR 종목(동적 — 로테이션 보유도 공시 감시)
- 최근 7일 공시를 list.json으로 조회, news 테이블에 source='DART'로 저장(제목 앞 📋, url=DART 뷰어)
  → 개요 뉴스 카드·아침 브리핑 헤드라인에 자동으로 섞여 나옴 (기존 뉴스 파이프 재사용)
"""
import io
import os
import zipfile
from datetime import datetime, timedelta
from xml.etree import ElementTree

import requests

WATCH_STATIC = ["005930", "010950"]        # 보유 (삼성전자, S-Oil) — 바뀌면 여기 수정


def _ensure(con):
    con.execute("CREATE TABLE IF NOT EXISTS dart_corp ("
                "stock_code TEXT PRIMARY KEY, corp_code TEXT, name TEXT)")
    from src.collectors.news import _ensure as news_ensure

    news_ensure(con)


def _corp_map(con, key):
    if con.execute("SELECT COUNT(*) c FROM dart_corp").fetchone()["c"] > 0:
        return
    r = requests.get("https://opendart.fss.or.kr/api/corpCode.xml",
                     params={"crtfc_key": key}, timeout=90)
    r.raise_for_status()
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    rows = []
    for el in ElementTree.fromstring(zf.read(zf.namelist()[0])).iter("list"):
        sc = (el.findtext("stock_code") or "").strip()
        if len(sc) == 6:                    # 상장사만
            rows.append((sc, (el.findtext("corp_code") or "").strip(),
                         (el.findtext("corp_name") or "").strip()))
    con.executemany("INSERT OR REPLACE INTO dart_corp VALUES (?,?,?)", rows)
    con.commit()
    print(f"  [dart] corp_code 매핑 {len(rows)}종목 적재")


def _watch(con) -> list:
    codes = set(WATCH_STATIC)
    try:                                    # 로테이션 KR 슬롯도 감시
        codes |= {str(r["symbol"]) for r in con.execute("SELECT symbol FROM rotation_slots")
                  if str(r["symbol"]).isdigit()}
    except Exception:
        pass
    return sorted(codes)


def collect(con) -> int:
    key = os.getenv("DART_API_KEY")
    if not key:
        return 0
    _ensure(con)
    _corp_map(con, key)
    bgn = (datetime.now() - timedelta(days=7)).strftime("%Y%m%d")
    n = 0
    for sc in _watch(con):
        row = con.execute("SELECT corp_code, name FROM dart_corp WHERE stock_code=?",
                          (sc,)).fetchone()
        if not row:
            continue
        r = requests.get("https://opendart.fss.or.kr/api/list.json",
                         params={"crtfc_key": key, "corp_code": row["corp_code"],
                                 "bgn_de": bgn, "page_count": "20"}, timeout=15)
        d = r.json() if r.ok else {}
        if d.get("status") == "013":        # 조회 결과 없음 — 정상
            continue
        if d.get("status") != "000":
            print(f"  [dart] {sc}: status {d.get('status')} {str(d.get('message'))[:50]}")
            continue
        for it in d.get("list", []):
            rd = it.get("rcept_dt", "")
            dt = f"{rd[:4]}-{rd[4:6]}-{rd[6:]}T09:00:00" if len(rd) == 8 else \
                datetime.now().isoformat(timespec="seconds")
            n += con.execute(
                "INSERT OR IGNORE INTO news (url, dt, market, code, source, title, keyword) "
                "VALUES (?,?,?,?,?,?,?)",
                (f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={it['rcept_no']}",
                 dt, "KR", sc, "DART", f"📋 {it.get('report_nm', '').strip()}",
                 row["name"])).rowcount
    con.commit()
    return n


if __name__ == "__main__":
    import sys

    from dotenv import load_dotenv

    load_dotenv()
    sys.path.insert(0, ".")
    from src import db

    c = db.connect()
    print("적재:", collect(c))
    for r in c.execute("SELECT dt, keyword, substr(title,1,44) t FROM news "
                       "WHERE source='DART' ORDER BY dt DESC LIMIT 8"):
        print(f"  {r['dt'][:10]} [{r['keyword']}] {r['t']}")
    c.close()
