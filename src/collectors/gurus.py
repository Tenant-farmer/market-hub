"""SEC EDGAR 13F 수집 — 구루 포트폴리오 + 분기 대비 변화.

흐름: 매니저 CIK → submissions JSON → 최근 13F-HR(/A) 목록
     → 분기별 최신 제출본만 채택(정정공시가 원본 대체)
     → filing index.json → information table XML 파싱 → guru_holdings
     → 최근 2개 분기 diff → guru_changes (new/add/trim/exit)

규정: 10 req/s 제한(요청당 0.15s 딜레이), 연락처 포함 User-Agent 필수.
값 단위: 2023년부터 13F value는 천달러가 아닌 달러 단위.
CUSIP→티커 매핑은 미구현 — 발행사명(nameOfIssuer)으로 표시.
"""
import os
import time
import xml.etree.ElementTree as ET

import requests

from src import config, db

SUB_URL = "https://data.sec.gov/submissions/CIK{cik10}.json"
ARCH_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}"

HOLD_COLS = ["accession", "cusip", "ticker", "name", "shares", "value_usd", "pct"]
CHANGE_COLS = ["cik", "quarter", "cusip", "ticker", "name", "action", "delta_shares", "delta_value"]


def _ua() -> dict:
    return {"User-Agent": os.getenv("EDGAR_USER_AGENT") or "market-hub/0.1 (research)"}


def _get(url: str) -> requests.Response:
    time.sleep(0.15)  # 10 req/s 제한 준수
    r = requests.get(url, headers=_ua(), timeout=60)
    r.raise_for_status()
    return r


def _quarter(report_date: str) -> str:
    y, m = report_date[:4], int(report_date[5:7])
    return f"{y}Q{(m - 1) // 3 + 1}"


def _ensure_name_col(con):
    cols = [r["name"] for r in con.execute("PRAGMA table_info(guru_changes)")]
    if "name" not in cols:
        con.execute("ALTER TABLE guru_changes ADD COLUMN name TEXT")


def collect(con) -> int:
    _ensure_name_col(con)
    cfg = config.load()["gurus"]
    total = 0
    for m in cfg["managers"]:
        try:
            total += _collect_manager(con, m["cik"], m["name"], cfg["quarters"])
        except Exception as e:
            print(f"[gurus] {m['name']} skip: {e}")
    _compute_changes(con)
    return total


def _collect_manager(con, cik: str, name: str, quarters: int) -> int:
    sub = _get(SUB_URL.format(cik10=str(int(cik)).zfill(10))).json()
    recent = sub["filings"]["recent"]
    by_q: dict[str, dict] = {}
    for form, acc, rdate, fdate in zip(
        recent["form"], recent["accessionNumber"], recent["reportDate"], recent["filingDate"]
    ):
        if form in ("13F-HR", "13F-HR/A") and rdate:
            q = _quarter(rdate)
            f = {"acc": acc, "filed": fdate, "amend": form.endswith("/A")}
            if q not in by_q or fdate > by_q[q]["filed"]:
                by_q[q] = f

    rows_n = 0
    for q, f in sorted(by_q.items(), reverse=True)[:quarters]:
        if con.execute("SELECT 1 FROM guru_filings WHERE accession=?", (f["acc"],)).fetchone():
            continue
        holdings = _fetch_holdings(cik, f["acc"])
        if not holdings:
            print(f"[gurus] {name} {q}: infotable 없음, 건너뜀")
            continue
        holdings, tot_val = _normalize_units(holdings)
        con.execute(
            "INSERT OR REPLACE INTO guru_filings "
            "(accession, cik, manager_name, quarter, filed_date, is_amendment) VALUES (?,?,?,?,?,?)",
            (f["acc"], str(cik), name, q, f["filed"], int(f["amend"])),
        )
        hrows = [
            (f["acc"], cusip, None, nm, sh, val, round(val / tot_val * 100, 2))
            for cusip, nm, val, sh in holdings
        ]
        rows_n += db.upsert(con, "guru_holdings", HOLD_COLS, hrows)
        print(f"[gurus] {name} {q}: {len(holdings)} 종목 (제출일 {f['filed']})")
    return rows_n


def _normalize_units(holdings: list[tuple]) -> tuple[list[tuple], float]:
    """2023년부터 달러 단위 보고가 원칙이나 일부 제출자는 여전히 천달러 단위.

    유명 매니저 포트가 $10M 미만일 리 없으므로 총액이 그보다 작으면 천 배 보정.
    반환: (보정된 holdings, 총액)
    """
    tot_val = sum(v for _, _, v, _ in holdings) or 1.0
    if tot_val < 1e7:
        holdings = [(c, n, v * 1000, s) for c, n, v, s in holdings]
        tot_val *= 1000
    return holdings, tot_val


def _fetch_holdings(cik: str, acc: str) -> list[tuple]:
    """information table XML을 찾아 (cusip, name, value, shares)로 집계."""
    base = ARCH_URL.format(cik=int(cik), acc=acc.replace("-", ""))
    idx = _get(base + "/index.json").json()
    xml_names = [
        i["name"] for i in idx["directory"]["item"]
        if i["name"].lower().endswith(".xml") and "primary_doc" not in i["name"].lower()
    ]
    for fn in xml_names:
        entries = _parse_infotable(_get(base + "/" + fn).content)
        if entries:
            agg: dict[str, list] = {}
            for cusip, nm, val, sh, putcall in entries:  # 동일 CUSIP 복수행(의결권 구분) 합산
                # 풋/콜 옵션은 주식 보유와 반대 방향일 수 있어 별도 항목으로 구분
                if putcall:
                    cusip = f"{cusip}-{putcall[0].upper()}"
                    nm = f"{nm} ({putcall.upper()})"
                c = agg.setdefault(cusip, [nm, 0.0, 0.0])
                c[1] += val
                c[2] += sh
            return [(cusip, v[0], v[1], v[2]) for cusip, v in agg.items()]
    return []


def _parse_infotable(data: bytes) -> list[tuple]:
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return []
    out = []
    for el in root.iter():
        if el.tag.split("}")[-1] != "infoTable":
            continue
        d = {c.tag.split("}")[-1]: c.text for c in el.iter() if c.text}
        try:
            out.append((
                d["cusip"].strip(),
                d["nameOfIssuer"].strip(),
                float(d["value"]),
                float(d.get("sshPrnamt", 0)),
                (d.get("putCall") or "").strip(),
            ))
        except (KeyError, ValueError):
            continue
    return out


def _compute_changes(con) -> None:
    """매니저별 최근 2개 분기 보유 diff → guru_changes."""
    con.execute("DELETE FROM guru_changes")
    ciks = [r["cik"] for r in con.execute("SELECT DISTINCT cik FROM guru_filings")]
    for cik in ciks:
        qs = con.execute(
            "SELECT accession, quarter FROM guru_filings WHERE cik=? ORDER BY quarter DESC LIMIT 2",
            (cik,),
        ).fetchall()
        if len(qs) < 2:
            continue
        cur, prev = qs[0], qs[1]
        cur_h = {r["cusip"]: r for r in con.execute(
            "SELECT cusip, name, shares, value_usd FROM guru_holdings WHERE accession=?",
            (cur["accession"],))}
        prev_h = {r["cusip"]: r for r in con.execute(
            "SELECT cusip, name, shares, value_usd FROM guru_holdings WHERE accession=?",
            (prev["accession"],))}
        rows = []
        for cusip, r in cur_h.items():
            if cusip not in prev_h:
                rows.append((cik, cur["quarter"], cusip, None, r["name"], "new",
                             r["shares"], r["value_usd"]))
            else:
                p = prev_h[cusip]
                dsh = r["shares"] - p["shares"]
                if p["shares"] and abs(dsh) / p["shares"] >= 0.05:  # 5% 미만 변화는 노이즈
                    rows.append((cik, cur["quarter"], cusip, None, r["name"],
                                 "add" if dsh > 0 else "trim",
                                 dsh, r["value_usd"] - p["value_usd"]))
        for cusip, p in prev_h.items():
            if cusip not in cur_h:
                rows.append((cik, cur["quarter"], cusip, None, p["name"], "exit",
                             -p["shares"], -p["value_usd"]))
        db.upsert(con, "guru_changes", CHANGE_COLS, rows)
