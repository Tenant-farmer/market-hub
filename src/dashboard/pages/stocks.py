"""종목 리서치 허브 (/stocks) — 시장·시총구간·섹터 필터 + 검색 + 정렬."""
from urllib.parse import urlencode

from flask import Blueprint, render_template, request

from src import db
from src.dashboard import queries
from src.dashboard.queries import CAP_BUCKETS

bp = Blueprint("stocks", __name__)

PER = 50


@bp.get("/stocks")
def stocks():
    mkt = request.args.get("mkt", "us")
    if mkt not in ("kr", "us"):
        mkt = "us"
    cap = request.args.get("cap", "all")
    if cap not in {b[0] for b in CAP_BUCKETS[mkt]}:
        cap = "all"
    sector = (request.args.get("sector") or "").strip() or None
    sort = request.args.get("sort", "mcap")
    if sort not in ("mcap", "vol", "score"):
        sort = "mcap"
    q = (request.args.get("q") or "").strip() or None
    try:
        page = max(1, int(request.args.get("page", 1)))
    except ValueError:
        page = 1

    con = db.connect()
    hub = queries.stock_hub(con, mkt, cap, sector, sort, q, page, PER)
    con.close()

    def url(**over):
        prm = {"mkt": mkt, "cap": cap, "sector": sector, "sort": sort, "q": q}
        prm.update(over)
        return "/stocks?" + urlencode({k: v for k, v in prm.items() if v})

    mkt_pills = [
        ("한국 주식", url(mkt="kr", cap="all", sector=None), mkt == "kr"),
        ("미국 주식", url(mkt="us", cap="all", sector=None), mkt == "us"),
    ]
    cap_pills = [(label, url(cap=key), cap == key) for key, label, _, _ in CAP_BUCKETS[mkt]]
    sector_pills = [("전체 섹터", url(sector=None), sector is None)] + [
        (s, url(sector=s), sector == s) for s in (hub["sectors"] if hub else [])
    ]
    sort_pills = [
        ("시가총액순", url(sort="mcap"), sort == "mcap"),
        ("거래량순", url(sort="vol"), sort == "vol"),
        ("주도주순", url(sort="score"), sort == "score"),
    ]
    pages = hub["pages"] if hub else 1
    lo = max(1, page - 3)
    page_links = [(p, url(page=p) if p > 1 else url(), p == page)
                  for p in range(lo, min(pages, lo + 6) + 1)]

    return render_template(
        "stocks.html",
        hub=hub, mkt=mkt, sort=sort, q=q or "", cap=cap, sector=sector,
        mkt_pills=mkt_pills, cap_pills=cap_pills, sector_pills=sector_pills,
        sort_pills=sort_pills, page=page, page_links=page_links,
        next_url=url(page=page + 1) if hub and page < hub["pages"] else None,
        rank_base=(page - 1) * PER,
    )
