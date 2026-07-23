"""뉴스 수집 — 네이버 검색 API(KR, 키 있으면) / Google News RSS(폴백) + yfinance news(US).

- KR: NAVER_CLIENT_ID/SECRET 있으면 네이버 뉴스 검색 (품질·속보성 우위) — 감시 대상은
  시장 키워드 + 고정 보유 + rotation_slots KR 종목 **동적** (이름은 dart_corp 매핑).
  키 없으면 기존 Google RSS 고정 키워드로 폴백
- US: yf.Ticker(sym).news (버전에 따라 응답 형태가 달라 방어적으로 파싱)
- 저장: news 테이블, url PRIMARY KEY 로 중복 제거 (INSERT OR IGNORE)
"""
import html
import os
import re
from datetime import datetime
from email.utils import parsedate_to_datetime
from urllib.parse import quote
from xml.etree import ElementTree

import requests
import yfinance as yf

KR_QUERIES = [("코스피", None), ("삼성전자", "005930"), ("S-Oil", "010950")]
KR_STATIC = {"005930": "삼성전자", "010950": "S-Oil"}   # dart_corp 없을 때 이름 폴백
US_SYMBOLS = ["SPY", "QQQ", "AAPL"]
MEGACAPS = ["NVDA", "MSFT", "GOOGL", "AMZN", "TSLA", "MU"]   # AAPL은 US_SYMBOLS에 이미
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def _ensure(con):
    con.execute(
        "CREATE TABLE IF NOT EXISTS news ("
        "url TEXT PRIMARY KEY, dt TEXT, market TEXT, code TEXT, "
        "source TEXT, title TEXT, keyword TEXT)"
    )
    con.execute("CREATE INDEX IF NOT EXISTS idx_news_dt ON news(dt)")
    try:                                                # 기사 요약 컬럼 (마이그레이션 안전)
        con.execute("ALTER TABLE news ADD COLUMN summary TEXT")
        con.commit()
    except Exception:
        pass


def _iso_local(dt) -> str:
    try:
        return dt.astimezone().replace(tzinfo=None).isoformat(timespec="seconds")
    except Exception:
        return datetime.now().isoformat(timespec="seconds")


def _google_rss(keyword: str) -> list[dict]:
    r = requests.get(
        f"https://news.google.com/rss/search?q={quote(keyword)}&hl=ko&gl=KR&ceid=KR:ko",
        headers=UA, timeout=15,
    )
    r.raise_for_status()
    out = []
    for it in ElementTree.fromstring(r.content).findall(".//item")[:12]:
        title = (it.findtext("title") or "").strip()
        url = (it.findtext("link") or "").strip()
        if not title or not url:
            continue
        try:
            dt = _iso_local(parsedate_to_datetime(it.findtext("pubDate") or ""))
        except Exception:
            dt = datetime.now().isoformat(timespec="seconds")
        out.append({"title": title, "url": url, "dt": dt,
                    "source": (it.findtext("source") or "").strip()})
    return out


def _naver(keyword: str) -> list[dict]:
    r = requests.get(
        "https://openapi.naver.com/v1/search/news.json",
        params={"query": keyword, "display": 15, "sort": "date"},
        headers={"X-Naver-Client-Id": os.getenv("NAVER_CLIENT_ID", ""),
                 "X-Naver-Client-Secret": os.getenv("NAVER_CLIENT_SECRET", "")},
        timeout=15)
    r.raise_for_status()
    out = []
    for it in r.json().get("items", []):
        title = html.unescape(re.sub(r"</?b>", "", it.get("title") or "")).strip()
        url = (it.get("originallink") or it.get("link") or "").strip()
        if not title or not url:
            continue
        try:
            dt = _iso_local(parsedate_to_datetime(it.get("pubDate") or ""))
        except Exception:
            dt = datetime.now().isoformat(timespec="seconds")
        summ = html.unescape(re.sub(r"</?b>", "", it.get("description") or "")).strip()[:200]
        out.append({"title": title, "url": url, "dt": dt, "source": "NAVER",
                    "summary": summ})
    return out


def _kr_watch(con) -> list[tuple]:
    """KR 검색어 — 시장 + 고정 보유 + 로테이션 슬롯 (이름은 dart_corp, 폴백 KR_STATIC)."""
    queries, codes = [("코스피", None)], list(KR_STATIC)
    try:
        codes += [str(r["symbol"]) for r in con.execute("SELECT symbol FROM rotation_slots")
                  if str(r["symbol"]).isdigit()]
    except Exception:
        pass
    for c in dict.fromkeys(codes):                     # 순서 보존 중복 제거
        try:
            row = con.execute("SELECT name FROM dart_corp WHERE stock_code=?", (c,)).fetchone()
        except Exception:
            row = None
        name = row["name"] if row else KR_STATIC.get(c)
        if name:
            queries.append((name, c))
    return queries


def _yf_news(sym: str) -> list[dict]:
    out = []
    for it in (yf.Ticker(sym).news or [])[:8]:
        c = it.get("content") if isinstance(it.get("content"), dict) else it   # 신/구 형태
        title = (c.get("title") or "").strip()
        url = (((c.get("canonicalUrl") or {}).get("url") if isinstance(c.get("canonicalUrl"), dict)
                else None)
               or ((c.get("clickThroughUrl") or {}).get("url")
                   if isinstance(c.get("clickThroughUrl"), dict) else None)
               or it.get("link") or "")
        if not title or not url:
            continue
        dt = datetime.now()
        if c.get("pubDate"):
            try:
                dt = datetime.fromisoformat(str(c["pubDate"]).replace("Z", "+00:00"))
            except ValueError:
                pass
        elif it.get("providerPublishTime"):
            dt = datetime.fromtimestamp(it["providerPublishTime"])
        prov = c.get("provider")
        out.append({"title": title, "url": url, "dt": _iso_local(dt),
                    "source": (prov.get("displayName") if isinstance(prov, dict)
                               else it.get("publisher") or ""),
                    "summary": (c.get("summary") or "").strip()[:200]})
    return out


def collect(con) -> int:
    _ensure(con)
    n = 0
    use_naver = bool(os.getenv("NAVER_CLIENT_ID") and os.getenv("NAVER_CLIENT_SECRET"))
    kr_queries = _kr_watch(con) if use_naver else KR_QUERIES
    for kw, code in kr_queries:
        try:
            for it in (_naver(kw) if use_naver else _google_rss(kw)):
                n += con.execute(
                    "INSERT OR IGNORE INTO news "
                    "(url, dt, market, code, source, title, keyword, summary) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (it["url"], it["dt"], "KR", code, it["source"], it["title"], kw,
                     it.get("summary") or ""),
                ).rowcount
        except Exception as e:                        # 키워드 하나 실패해도 나머지 계속
            print(f"  [news] KR '{kw}' 실패: {str(e)[:60]}")
    # US 감시: 기본 + 메가캡 + 로테이션 슬롯 (실적 알림의 관련 뉴스 재료)
    us_syms = list(US_SYMBOLS) + MEGACAPS
    try:
        us_syms += [str(r["symbol"]) for r in con.execute("SELECT symbol FROM rotation_slots")
                    if not str(r["symbol"]).isdigit()]
    except Exception:
        pass
    for sym in dict.fromkeys(us_syms):
        try:
            for it in _yf_news(sym):
                n += con.execute(
                    "INSERT OR IGNORE INTO news "
                    "(url, dt, market, code, source, title, keyword, summary) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (it["url"], it["dt"], "US", sym, it["source"], it["title"], sym,
                     it.get("summary") or ""),
                ).rowcount
        except Exception as e:
            print(f"  [news] US '{sym}' 실패: {str(e)[:60]}")
    con.commit()
    return n


if __name__ == "__main__":
    from src import db

    c = db.connect()
    print("적재:", collect(c))
    for r in c.execute("SELECT dt, market, keyword, substr(title,1,50) t FROM news "
                       "ORDER BY dt DESC LIMIT 8"):
        print(f"  {r['dt']} [{r['market']}/{r['keyword']}] {r['t']}")
    c.close()
