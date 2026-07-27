"""SEC EDGAR Form 4 — 내부자(임원·이사·10%주주) 매매 수집.

배경: 우리 팩터는 전부 가격·거래량 파생이라 서로 상관이 높다. Form 4는 **가격과 독립된
정보원**(경영진의 실제 자금 이동)이라, 상관 낮은 팩터가 될 가능성이 있다.
"내부자는 여러 이유로 팔지만, 사는 이유는 하나뿐"(피터 린치) — **매수만** 신호로 본다.

수집: 감시 종목(로테이션 US + 메가캡 + 보유) → CIK 해석 → 최근 Form 4 목록 →
      XML 파싱 → 코드 P(공개시장 매수)/S(매도)만 채택 → insider_trades 테이블
규정: 10 req/s (요청 0.15s 딜레이), User-Agent 필수 (gurus.py와 동일 규약)
"""
import os
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

import requests

TICKER_URL = "https://www.sec.gov/files/company_tickers.json"
SUB_URL = "https://data.sec.gov/submissions/CIK{cik10}.json"
ARCH = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}"
COLS = ["accession", "symbol", "filed_date", "trade_date", "insider", "title",
        "code", "shares", "price", "value_usd", "own_after"]


def _ua():
    return {"User-Agent": os.getenv("EDGAR_USER_AGENT") or "market-hub/0.1 (research)"}


def _get(url):
    time.sleep(0.15)                                   # SEC 10 req/s 준수
    r = requests.get(url, headers=_ua(), timeout=20)
    r.raise_for_status()
    return r


def _ensure(con):
    con.execute("""CREATE TABLE IF NOT EXISTS insider_trades (
        accession TEXT, symbol TEXT, filed_date TEXT, trade_date TEXT,
        insider TEXT, title TEXT, code TEXT, shares REAL, price REAL,
        value_usd REAL, own_after REAL,
        PRIMARY KEY (accession, symbol, insider, trade_date, code, shares))""")
    con.execute("CREATE INDEX IF NOT EXISTS idx_insider_sym ON insider_trades(symbol, filed_date)")


_TICKER_CACHE: dict = {}


def _cik_map():
    """티커 → CIK(10자리). SEC 공식 매핑, 프로세스 캐시."""
    if _TICKER_CACHE:
        return _TICKER_CACHE
    d = _get(TICKER_URL).json()
    for v in d.values():
        _TICKER_CACHE[v["ticker"].upper()] = str(v["cik_str"]).zfill(10)
    return _TICKER_CACHE


def _watch(con) -> list:
    """감시: 로테이션 US 슬롯 + 메가캡 + 기본 보유."""
    syms = {"AAPL"}
    try:
        from src.collectors.news import MEGACAPS

        syms |= set(MEGACAPS)
    except Exception:
        pass
    try:
        syms |= {str(r["symbol"]) for r in con.execute("SELECT symbol FROM rotation_slots")
                 if not str(r["symbol"]).isdigit()}
    except Exception:
        pass
    return sorted(syms)


def _parse_form4(data: bytes, symbol: str, accession: str) -> list[tuple]:
    """Form 4 XML → 비파생 거래(테이블 I) 행. 코드 P(매수)/S(매도)만."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return []
    name = (root.findtext(".//reportingOwnerId/rptOwnerName") or "").strip()
    rel = root.find(".//reportingOwnerRelationship")
    title = ""
    if rel is not None:
        bits = [t for t, tag in (("이사", "isDirector"), ("임원", "isOfficer"),
                                 ("10%주주", "isTenPercentOwner"))
                if (rel.findtext(tag) or "0") in ("1", "true")]
        title = "·".join(bits) or (rel.findtext("officerTitle") or "").strip()
    filed = (root.findtext(".//periodOfReport") or "").strip()
    out = []
    for tx in root.findall(".//nonDerivativeTransaction"):
        code = (tx.findtext(".//transactionCoding/transactionCode") or "").strip()
        if code not in ("P", "S"):                     # P=공개시장 매수, S=매도만
            continue
        def _f(path):
            v = tx.findtext(path)
            try:
                return float(v)
            except (TypeError, ValueError):
                return None
        shares = _f(".//transactionShares/value")
        price = _f(".//transactionPricePerShare/value")
        own = _f(".//postTransactionAmounts/sharesOwnedFollowingTransaction/value")
        tdate = (tx.findtext(".//transactionDate/value") or filed).strip()
        if not shares:
            continue
        out.append((accession, symbol, filed, tdate, name, title, code, shares, price,
                    (shares * price) if price else None, own))
    return out


def collect(con, days: int = 30) -> int:
    """감시 종목의 최근 Form 4 수집. 반환: 신규 저장 행 수."""
    _ensure(con)
    cmap = _cik_map()
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    n = 0
    for sym in _watch(con):
        cik10 = cmap.get(sym.upper())
        if not cik10:
            continue
        try:
            sub = _get(SUB_URL.format(cik10=cik10)).json()
            recent = sub.get("filings", {}).get("recent", {})
            forms = recent.get("form", [])
            for i, form in enumerate(forms):
                if form != "4" or recent["filingDate"][i] < since:
                    continue
                acc = recent["accessionNumber"][i].replace("-", "")
                cik = str(int(cik10))
                try:                                   # filing 내 XML 문서 찾기
                    idx = _get(f"{ARCH.format(cik=cik, acc=acc)}/index.json").json()
                    xmls = [x["name"] for x in idx["directory"]["item"]
                            if x["name"].endswith(".xml") and not x["name"].startswith("0")]
                    if not xmls:
                        continue
                    raw = _get(f"{ARCH.format(cik=cik, acc=acc)}/{xmls[0]}").content
                except Exception:
                    continue
                rows = _parse_form4(raw, sym, recent["accessionNumber"][i])
                for r in rows:
                    n += con.execute(
                        f"INSERT OR IGNORE INTO insider_trades ({','.join(COLS)}) "
                        f"VALUES ({','.join('?' * len(COLS))})", r).rowcount
        except Exception as e:
            print(f"  [insider] {sym} 실패: {str(e)[:60]}")
    con.commit()
    return n


def net_flow(con, days: int = 90, buys_only: bool = False) -> list[dict]:
    """종목별 내부자 순매수(매수액-매도액). buys_only=True면 매수 있는 종목만.

    해석 주의: 내부자 '매도'는 신호가 약하다(분산·세금·스톡옵션 행사 등 이유 다양).
    '매수'는 이유가 하나뿐(주가가 싸다고 믿음)이라 신호가 강하다 — 비대칭을 기억할 것.
    """
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    q = ("SELECT symbol, "
         "SUM(CASE WHEN code='P' THEN COALESCE(value_usd,0) ELSE 0 END) buy, "
         "SUM(CASE WHEN code='S' THEN COALESCE(value_usd,0) ELSE 0 END) sell, "
         "COUNT(DISTINCT CASE WHEN code='P' THEN insider END) n_buyers, "
         "COUNT(DISTINCT CASE WHEN code='S' THEN insider END) n_sellers "
         "FROM insider_trades WHERE trade_date >= ? GROUP BY symbol ")
    if buys_only:
        q += "HAVING buy > 0 "
    rows = con.execute(q + "ORDER BY (buy - sell) DESC", (since,)).fetchall()
    return [dict(r) | {"net": r["buy"] - r["sell"]} for r in rows]


def recent_buys(con, days: int = 90) -> list[dict]:
    """내부자 매수가 있는 종목만 (강한 신호)."""
    return net_flow(con, days, buys_only=True)


if __name__ == "__main__":
    import sys

    from dotenv import load_dotenv

    load_dotenv()
    sys.path.insert(0, ".")
    from src import db

    c = db.connect()
    print("수집:", collect(c), "행")
    flows = net_flow(c)
    buys = [f for f in flows if f["buy"] > 0]
    print(f"\n최근 90일 내부자 흐름 ({len(flows)}종목, 매수 있는 종목 {len(buys)}개)")
    print("  ⚠ 매수는 신호가 강하고(이유 하나), 매도는 약하다(분산·세금·옵션행사)")
    for r in flows[:12]:
        tag = "🟢매수우위" if r["net"] > 0 else "🔴매도우위"
        print(f"  {r['symbol']:6} {tag} 순 ${r['net']:>+14,.0f} "
              f"(매수 ${r['buy']:>12,.0f}/{r['n_buyers']}명 · 매도 ${r['sell']:>12,.0f}/{r['n_sellers']}명)")
    c.close()
