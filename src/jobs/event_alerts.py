"""지표·실적 발표 즉시 알림 — 워커가 5분마다 점검, 발표 확인 시 텔레그램.

- 경제지표(US·KR major): 발표시각(gmt+9h=KST) 도달 → Nasdaq API 당일 재조회로 actual 확인
  → "📊 CPI 발표: 3.2% (예상 3.1% · 이전 3.4%)". actual이 아직 비면 다음 사이클 재확인,
  30분 넘게 비면 예상치만으로 1회 알림(값 대기 표기), 2시간 지나면 조용히 종료
- 실적(감시 = 로테이션 US + 메가캡 + **섹터별 시총 상위 3** + AAPL, 44종목):
  ① 발표 후 리뷰(PEAD) 우선 — 실적 숫자 해석
  ② 리뷰가 안 나간 종목만 '발표 시간대' 예고 —
     장전(BMO) 당일 19:00 KST(=06:00 ET) / 장후(AMC·미표기) 익일 05:00 KST(=16:00 ET)
- 멱등: collector_runs('event_alert', message=키) — 이벤트당 1회
"""
import os
from datetime import datetime, timedelta

import requests

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Accept": "application/json"}
ECON_URL = "https://api.nasdaq.com/api/calendar/economicevents"
# 국기 표는 국가코드를 정의하는 수집기가 정본 — 사본을 두면 국가를 넓힐 때 어긋난다
from src.collectors.econ_calendar import FLAG  # noqa: E402


def _once(con, key: str) -> bool:
    """key 최초면 기록 후 True (이벤트당 1회 발신 보장)."""
    dup = con.execute("SELECT 1 FROM collector_runs WHERE collector='event_alert' "
                      "AND message=? LIMIT 1", (key,)).fetchone()
    if dup:
        return False
    con.execute("INSERT INTO collector_runs (collector, run_at, status, rows, message) "
                "VALUES ('event_alert', ?, 'ok', 0, ?)",
                (datetime.now().isoformat(timespec="seconds"), key))
    con.commit()
    return True


def _send(text: str) -> bool:
    """알림 발송. 실패해도 예외를 올리지 않되 **조용히 넘어가지도 않는다**.

    원래 `except: pass`라 텔레그램이 400을 내도 '발송함'으로 카운트됐다. 오늘 아침
    판정 알림이 `<` 때문에 400을 낸 것과 같은 함정 — 실패했다는 사실 자체를 알 수 없었다.
    """
    try:
        from src import notify

        notify.send(text)
        return True
    except Exception as e:
        try:
            from src.errlog import swallow

            swallow("event_alerts._send", e)
        except Exception:
            pass
        print(f"[event_alerts] 발송 실패: {type(e).__name__}: {str(e)[:120]}")
        return False


def _refresh_econ(con, d: str) -> None:
    """해당 날짜의 actual 값을 API에서 갱신 (1콜로 그날 전체)."""
    try:
        r = requests.get(ECON_URL, params={"date": d}, headers=UA, timeout=20)
        for row in (r.json().get("data") or {}).get("rows") or []:
            act = (row.get("actual") or "").strip()
            if act:                                    # 동명 이벤트(GDP QoQ/YoY) 구분: consensus 병행 매칭
                con.execute("UPDATE econ_calendar SET actual=? WHERE date=? AND event=? "
                            "AND consensus=?",
                            (act, d, (row.get("eventName") or "").strip(),
                             row.get("consensus") or ""))
        con.commit()
    except Exception:
        pass


def check(con, now: datetime | None = None) -> int:
    """발표 점검 — 보낸 알림 수 반환. now 주입은 테스트용."""
    now = now or datetime.now()
    n = 0

    # ---- 경제지표 (major) — 발표시각 지난 것 중 미알림 ----
    try:
        rows = con.execute(
            "SELECT date, gmt, country, event, actual, consensus, previous FROM econ_calendar "
            "WHERE major=1 AND gmt != '' AND date >= ?",
            ((now - timedelta(days=1)).date().isoformat(),)).fetchall()
    except Exception:
        rows = []
    refreshed = set()
    for r in rows:
        try:
            from src.timeutil import et_to_kst   # gmt는 ET — +9h면 4시간 일찍 잡는다

            _t = et_to_kst(r["date"], r["gmt"])
            if _t is None:
                continue
            rel = _t.replace(tzinfo=None)
        except ValueError:
            continue
        age = (now - rel).total_seconds()
        if age < 0 or age > 7200:                      # 아직 전 / 2시간 경과 → 스킵
            continue
        key = f"econ_{r['date']}_{r['event']}_{r['consensus']}"
        if con.execute("SELECT 1 FROM collector_runs WHERE collector='event_alert' "
                       "AND message=? LIMIT 1", (key,)).fetchone():
            continue
        actual = (r["actual"] or "").strip()
        if not actual and r["date"] not in refreshed:  # 값 재조회 (날짜당 1콜/사이클)
            _refresh_econ(con, r["date"])
            refreshed.add(r["date"])
            r2 = con.execute("SELECT actual FROM econ_calendar WHERE date=? AND event=? "
                             "AND consensus=?",
                             (r["date"], r["event"], r["consensus"] or "")).fetchone()
            actual = (r2["actual"] or "").strip() if r2 else ""
        if not actual and age < 1800:                  # 30분까진 값 기다림
            continue
        if _once(con, key):
            val = (f"실제 <b>{actual}</b>" if actual else "값 대기 중")
            tail = " · ".join(x for x in (
                f"예상 {r['consensus']}" if r["consensus"] else "",
                f"이전 {r['previous']}" if r["previous"] else "") if x)
            _send(f"📊 {FLAG.get(r['country'], '')} <b>{r['event']}</b> 발표 — {val}"
                  + (f" ({tail})" if tail else ""))
            n += 1

    # ---- 실적 — 감시: 로테이션 US + 보유 + 메가캡 + 섹터별 시총 1위 ----
    watch = {"AAPL"}
    try:
        from src.collectors.news import MEGACAPS

        watch |= set(MEGACAPS)
    except Exception:
        pass
    try:
        watch |= {str(x["symbol"]) for x in con.execute("SELECT symbol FROM rotation_slots")
                  if not str(x["symbol"]).isdigit()}
    except Exception:
        pass
    # 섹터별 시총 상위 N (US) — 1위만 보면 섹터 대표 대형주가 줄줄이 빠진다.
    # 2026-07-28: 코카콜라(KO) 실적에 알림이 안 왔다는 지적 — 필수소비재 1위가 아니라 제외됐다.
    # 실측 알림량(향후 14일): 1위 0.9건/일 · 3위 1.6건 · 5위 2.4건 · 8위 3.7건.
    # 3위면 섹터 대표 대형주가 대부분 들어오면서도 알림 피로가 없다. 11섹터 × 3 = 감시 44종목.
    try:
        top = int(os.getenv("EVENT_SECTOR_TOP", "3"))
        watch |= {r["symbol"] for r in con.execute(
            "SELECT symbol FROM (SELECT m.symbol, ROW_NUMBER() OVER "
            "(PARTITION BY sm.sector_name ORDER BY m.mcap DESC) rn "
            "FROM stock_meta m JOIN sector_map sm "
            "ON sm.stock_code=m.symbol AND sm.market='US_STOCK') WHERE rn<=?", (top,))}
    except Exception:
        pass
    # 리뷰(PEAD)를 **먼저** 내보내고, 리뷰가 나간 종목은 예고를 생략한다.
    # 장전(BMO) 발표는 예고 창이 열리는 19:00 KST에 이미 실적이 확정돼 있어, 예고와 리뷰가
    # 같은 사이클에 3초 간격으로 둘 다 갔다(2026-07-28 KO 실측). 예고가 예고 역할을 못 한다.
    # '장전이면 무조건 생략'이 아니라 '리뷰가 실제로 나갔을 때만 생략' — 리뷰 데이터가
    # 없는 종목까지 침묵하면 알림 자체가 사라진다. 장후(AMC)는 시차가 있어 2단이 유지된다.
    reviewed = _pead_alerts(con, watch, now)
    n += len(reviewed)

    try:
        ers = con.execute(
            "SELECT symbol, date, when_time, name, eps_forecast FROM earnings_calendar "
            "WHERE date >= ? AND date <= ?",
            ((now - timedelta(days=1)).date().isoformat(), now.date().isoformat())).fetchall()
    except Exception:
        ers = []
    for e in ers:
        if e["symbol"] not in watch or e["symbol"] in reviewed:
            continue
        pre = "pre" in (e["when_time"] or "")
        d = datetime.fromisoformat(e["date"])
        trig = d.replace(hour=19) if pre else (d + timedelta(days=1)).replace(hour=5)
        if not (trig <= now <= trig + timedelta(hours=6)):
            continue
        key = f"earn_{e['date']}_{e['symbol']}"
        if _once(con, key):
            eps = f" — 예상 EPS {e['eps_forecast']}" if e["eps_forecast"] else ""
            msg = (f"📈 <b>{e['symbol']}</b> 실적 발표 시간대 "
                   f"({'장전' if pre else '장 마감 후'}){eps} · {e['name'][:30]}")
            try:                                       # 관련 뉴스 2건 (제목 링크 + 요약 한 줄)
                for nr in con.execute(
                        "SELECT title, url, summary FROM news WHERE code=? "
                        "ORDER BY dt DESC LIMIT 2", (e["symbol"],)):
                    msg += f"\n· <a href=\"{nr['url']}\">{nr['title'][:60]}</a>"
                    if nr["summary"]:
                        msg += f"\n  {nr['summary'][:100]}"
            except Exception:
                pass
            _send(msg)
            n += 1

    return n


def _fmt_b(v):
    """금액 → 조/억 단위 축약 (USD)."""
    if v is None:
        return "–"
    a = abs(v)
    for div, unit in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if a >= div:
            return f"${v / div:,.2f}{unit}"
    return f"${v:,.0f}"


def _earnings_detail(sym: str) -> dict | None:
    """애널리스트가 실제로 보는 항목들을 yfinance에서 수집.

    EPS 서프라이즈만으로는 판단이 안 된다 — 매출(조작 어려움)·마진(가격결정력)·
    서프라이즈 연속성(SUE 대용)·발표 후 주가반응(시장 해석)을 함께 본다.
    """
    import yfinance as yf

    t = yf.Ticker(sym)
    d = {}
    try:                                            # ① EPS 서프라이즈 + 과거 8분기 이력
        h = t.earnings_history
        if h is None or len(h) == 0:
            return None
        h = h.reset_index()
        last = h.iloc[-1]
        act, est = last.get("epsActual"), last.get("epsEstimate")
        if act is None or est is None or not est:
            return None
        d["eps_act"], d["eps_est"] = float(act), float(est)
        d["eps_surprise"] = (float(act) - float(est)) / abs(float(est))
        # SUE 대용: 과거 서프라이즈의 표준편차로 표준화 (문서 공식)
        past = h.dropna(subset=["epsActual", "epsEstimate"])
        errs = (past["epsActual"] - past["epsEstimate"]).astype(float)
        sd = float(errs.std()) if len(errs) >= 3 else None
        d["sue"] = (float(act) - float(est)) / sd if sd else None
        # 연속성: 최근 4분기 중 몇 번 beat 했나 (첫 서프라이즈가 신호 강함)
        recent = past.tail(4)
        d["beat_streak"] = int(((recent["epsActual"] - recent["epsEstimate"]) > 0).sum())
        d["n_hist"] = len(recent)
    except Exception:
        return None
    try:                                            # ② 매출 + 마진 (신호 가중치 High)
        fin = t.quarterly_financials
        if fin is not None and not fin.empty:
            def _row(keys):
                for k in keys:
                    if k in fin.index:
                        v = fin.loc[k].dropna()
                        if len(v) >= 2:
                            return float(v.iloc[0]), float(v.iloc[1])
                return None, None
            rev, rev_p = _row(["Total Revenue", "Operating Revenue"])
            op, op_p = _row(["Operating Income", "EBIT"])
            gp, _ = _row(["Gross Profit"])
            d["revenue"] = rev
            d["rev_qoq"] = (rev / rev_p - 1) if (rev and rev_p) else None
            d["op_margin"] = (op / rev) if (op and rev) else None
            d["op_margin_prev"] = (op_p / rev_p) if (op_p and rev_p) else None
            d["gross_margin"] = (gp / rev) if (gp and rev) else None
    except Exception:
        pass
    try:                                            # ③ 발표 후 주가 반응 (시장의 해석)
        px = t.history(period="5d")["Close"]
        if len(px) >= 2:
            d["px_react"] = float(px.iloc[-1] / px.iloc[0] - 1)
    except Exception:
        pass
    try:                                            # ④ 밸류에이션 맥락
        info = t.info
        d["fwd_pe"] = info.get("forwardPE")
        d["name"] = info.get("shortName")
    except Exception:
        pass
    return d


def _pead_alerts(con, watch, now) -> set:
    """발표 후 애널리스트式 실적 리뷰 알림 — EPS·매출·마진·SUE·주가반응 + PEAD 기대치.

    근거(scripts/pead_backtest.py, 1,797 이벤트): 서프라이즈 상위20%는 63일 시장초과 +9.4%
    (승률 59%), 하위20%는 -6.2%(승률 32%). **미스 회피가 더 강한 엣지** — 보유 중이면 경고.
    문서 가중치: 매출>EPS(조작 난이도), 마진=가격결정력, 첫 서프라이즈>연속 서프라이즈.
    """
    sent = set()                       # 반환: **리뷰를 보낸 종목** — 예고 중복 억제에 쓴다
    for sym in watch:
        row = con.execute(
            "SELECT symbol, date FROM earnings_calendar WHERE symbol=? AND date <= ? "
            "AND date >= ? ORDER BY date DESC LIMIT 1",
            (sym, now.date().isoformat(),
             (now - timedelta(days=3)).date().isoformat())).fetchone()
        if not row:
            continue
        key = f"pead_{row['date']}_{sym}"
        if con.execute("SELECT 1 FROM collector_runs WHERE collector='event_alert' "
                       "AND message=? LIMIT 1", (key,)).fetchone():
            continue
        d = _earnings_detail(sym)
        if not d:
            continue
        if not _once(con, key):
            continue
        s = d["eps_surprise"]
        held = con.execute("SELECT 1 FROM rotation_slots WHERE symbol=? LIMIT 1",
                           (sym,)).fetchone() is not None

        # ---- 헤드라인 (등급) ----
        # 서프라이즈율(백테스트 분위 기준) 또는 SUE(문서 기준 |2|+)가 강하면 상위 등급.
        # 서프라이즈율만 쓰면 '예측오차가 작은 기업의 +21%'를 놓친다(SUE 2.2인데 부합 판정 버그).
        sue = d.get("sue")
        strong_up = s >= 0.25 or (sue is not None and sue >= 2 and s > 0.02)
        strong_dn = s <= -0.04 or (sue is not None and sue <= -2)
        if strong_up:
            tag, pead = "🟢 대형 서프라이즈", "63일 평균 <b>+9.4%</b> (승률 59%)"
        elif strong_dn:
            tag, pead = "🔴 실적 미스", "63일 평균 <b>-6.2%</b> (승률 32%)"
        elif s > 0.02:
            tag, pead = "🟡 소폭 beat", "63일 평균 +1% 내외 (약한 양)"
        else:
            tag, pead = "⚪ 컨센서스 부합", "신호 약함 (63일 ~-1%)"
        L = [f"{tag} <b>{sym}</b>" + (f" · {d['name'][:24]}" if d.get("name") else ""),
             f"보유: {'예 (로테이션)' if held else '아니오'}", ""]

        # ---- 실적 상세 (애널리스트 순서: 매출 → 마진 → EPS) ----
        L.append("<b>📊 실적</b>")
        if d.get("revenue"):
            qoq = f" ({d['rev_qoq']:+.1%} QoQ)" if d.get("rev_qoq") is not None else ""
            L.append(f"• 매출 {_fmt_b(d['revenue'])}{qoq}")
        if d.get("op_margin") is not None:
            delta = ""
            if d.get("op_margin_prev") is not None:
                dm = (d["op_margin"] - d["op_margin_prev"]) * 100
                delta = f" ({dm:+.1f}%p vs 전분기 — {'개선' if dm > 0 else '악화'})"
            L.append(f"• 영업이익률 {d['op_margin']:.1%}{delta}")
        if d.get("gross_margin") is not None:
            L.append(f"• 매출총이익률 {d['gross_margin']:.1%}")
        L.append(f"• EPS {d['eps_act']:.2f} vs 예상 {d['eps_est']:.2f} "
                 f"(<b>{s:+.1%}</b>)")
        if d.get("sue") is not None:
            lvl = "매우 강함" if abs(d["sue"]) >= 2 else "보통" if abs(d["sue"]) >= 0.5 else "약함"
            L.append(f"• SUE {d['sue']:+.2f} ({lvl}) — 과거 예측오차 대비 표준화")
        if d.get("n_hist"):
            L.append(f"• 최근 {d['n_hist']}분기 중 {d['beat_streak']}회 beat"
                     + (" — 첫 서프라이즈(신호 강함)" if d["beat_streak"] <= 1 and s > 0 else ""))
        L.append("")

        # ---- 시장 반응 + 밸류에이션 ----
        bits = []
        if d.get("px_react") is not None:
            bits.append(f"발표 후 주가 {d['px_react']:+.1%}")
        if d.get("fwd_pe"):
            bits.append(f"선행 PER {d['fwd_pe']:.1f}배")
        if bits:
            L.append("<b>💹 시장 반응</b>")
            L.append("• " + " · ".join(bits))
            # 괴리 해석 (애널리스트가 가장 주목하는 부분)
            if d.get("px_react") is not None:
                if s > 0.05 and d["px_react"] < -0.02:
                    L.append("• ⚠ 호실적인데 주가 하락 — 가이던스 하향·기대치 과열 가능성")
                elif s < -0.02 and d["px_react"] > 0.02:
                    L.append("• 💡 미스인데 주가 상승 — 악재 선반영·가이던스 개선 가능성")
            L.append("")

        # ---- 관련 뉴스 (실적 해석의 맥락) ----
        try:
            nrows = con.execute(
                "SELECT title, url, summary, source FROM news WHERE code=? "
                "ORDER BY dt DESC LIMIT 3", (sym,)).fetchall()
            if nrows:
                L.append("<b>📰 관련 뉴스</b>")
                for nr in nrows:
                    src = f" <i>({nr['source'][:14]})</i>" if nr["source"] else ""
                    L.append(f"• <a href=\"{nr['url']}\">{nr['title'][:70]}</a>{src}")
                    if nr["summary"]:
                        L.append(f"  <i>{nr['summary'][:110]}</i>")
                L.append("")
        except Exception:
            pass

        # ---- 판단 ----
        L.append("<b>🎯 판단</b>")
        L.append(f"• PEAD 기대: {pead}")
        if s <= -0.04 and held:
            L.append("• <b>보유 중 + 미스 → 하방 주의</b> (청산규칙 손절-8%·주도이탈 감시)")
        elif s <= -0.04:
            L.append("• 신규 진입 회피 권장")
        elif s >= 0.25:
            L.append("• 모멘텀 지속 가능성 — 로테이션 다음 평가 시 순위 상승 여지")
        L.append("<i>근거: pead_backtest.py 1,797 이벤트 (2024-11~2026-06)</i>")
        _send("\n".join(L))
        sent.add(sym)
    return sent


if __name__ == "__main__":
    import sys

    from dotenv import load_dotenv

    load_dotenv()
    sys.path.insert(0, ".")
    from src import db

    c = db.connect()
    print("알림:", check(c))
    c.close()
