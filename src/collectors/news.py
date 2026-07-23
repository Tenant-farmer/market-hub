"""뉴스 수집 — Google News RSS(KR) + yfinance news(US). API 키 불요.

- KR: Google News RSS 키워드 검색 (시장 + 보유/관심 종목명). 무료·무키, 과도 호출만 피하면 안정적
- US: yf.Ticker(sym).news (버전에 따라 응답 형태가 달라 방어적으로 파싱)
- 저장: news 테이블, url PRIMARY KEY 로 중복 제거 (INSERT OR IGNORE)
- 보유/관심이 바뀌면 아래 KR_QUERIES/US_SYMBOLS 수정 (추후 보유 연동 자동화 여지)
"""
from datetime import datetime
from email.utils import parsedate_to_datetime
from urllib.parse import quote
from xml.etree import ElementTree

import requests
import yfinance as yf

KR_QUERIES = [("코스피", None), ("삼성전자", "005930"), ("S-Oil", "010950")]
US_SYMBOLS = ["SPY", "QQQ", "AAPL"]
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def _ensure(con):
    con.execute(
        "CREATE TABLE IF NOT EXISTS news ("
        "url TEXT PRIMARY KEY, dt TEXT, market TEXT, code TEXT, "
        "source TEXT, title TEXT, keyword TEXT)"
    )
    con.execute("CREATE INDEX IF NOT EXISTS idx_news_dt ON news(dt)")


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
                               else it.get("publisher") or "")})
    return out


def collect(con) -> int:
    _ensure(con)
    n = 0
    for kw, code in KR_QUERIES:
        try:
            for it in _google_rss(kw):
                n += con.execute(
                    "INSERT OR IGNORE INTO news (url, dt, market, code, source, title, keyword) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (it["url"], it["dt"], "KR", code, it["source"], it["title"], kw),
                ).rowcount
        except Exception as e:                        # 키워드 하나 실패해도 나머지 계속
            print(f"  [news] KR '{kw}' 실패: {str(e)[:60]}")
    for sym in US_SYMBOLS:
        try:
            for it in _yf_news(sym):
                n += con.execute(
                    "INSERT OR IGNORE INTO news (url, dt, market, code, source, title, keyword) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (it["url"], it["dt"], "US", sym, it["source"], it["title"], sym),
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
