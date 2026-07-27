"""매매 체결 알림 — 우리가 실제로 사고팔 때 텔레그램 통지.

워커가 매 폴 사이클 끝에 호출. 아직 알림 안 보낸 주문(notified IS NULL)을 조회해
전략·방향별로 묶어 1건으로 발송(로테이션 8종목이 8통 아니라 1통) 후 notified=1 마킹.
- 상태 표기: filled/submitted/accepted=체결(예정), rejected/canceled/stale=실패/취소
- 멱등: notified 컬럼으로 재전송 방지. 텔레그램 미설정이면 조용히 스킵(마킹만)
"""
import re
from datetime import datetime

STRAT = {"signal-entry": "🎯 신호진입", "rotation": "🔄 로테이션",
         "exit": "🛡 청산", "speed-test": "⚡ 테스트"}
OK = {"filled", "submitted", "accepted", "logged", "new", "partially_filled"}


def _ensure(con):
    try:
        con.execute("ALTER TABLE orders ADD COLUMN notified INTEGER")
        con.commit()
    except Exception:
        pass


def _cur(v, is_kr: bool) -> str:
    """통화 표기 — KR은 원(정수), US는 달러(소수 2자리)."""
    return f"{v:,.0f}원" if is_kr else f"${v:,.2f}"


def _fill(msg) -> tuple:
    """orders.message에서 실제 체결 수량·단가 파싱.

    키움: '0126213 filled 8@208000' / 알파카는 message가 UUID뿐이라 None 반환(주문값 사용).
    """
    m = re.search(r"filled\s+([\d.]+)\s*@\s*([\d.]+)", str(msg or ""))
    if m:
        try:
            return float(m.group(1)), float(m.group(2))
        except ValueError:
            pass
    return None, None


def _name(con, code: str) -> str:
    """KR 종목코드 → 한글명 (dart_corp 우선, 없으면 sector_map)."""
    for q in ("SELECT name FROM dart_corp WHERE stock_code=?",
              "SELECT name FROM sector_map WHERE stock_code=? LIMIT 1"):
        try:
            r = con.execute(q, (code,)).fetchone()
            if r and r["name"]:
                return r["name"]
        except Exception:
            pass
    return code


def _buy_reason(con, r) -> str:
    """매수 근거 한 줄 (신호의 strategy 필드)."""
    try:
        s = con.execute("SELECT strategy FROM signals WHERE id=?", (r["signal_id"],)).fetchone()
        return s["strategy"] if s and s["strategy"] else ""
    except Exception:
        return ""


def _sell_pnl(con, r) -> str:
    """매도 손익 — 청산 사유(손절 -10.8% 등)를 신호에서 가져온다."""
    try:
        s = con.execute("SELECT strategy FROM signals WHERE id=?", (r["signal_id"],)).fetchone()
        if s and s["strategy"]:
            return s["strategy"]                    # 예: '청산:손절 -10.8%'
    except Exception:
        pass
    return ""


def notify_new_orders(con) -> int:
    """미알림 주문을 묶어 발송, 발송 수 반환. 항상 notified 마킹(재전송 방지)."""
    _ensure(con)
    # 전략(source)은 orders.signal_id로 signals를 조인해 정확히 가져온다
    rows = con.execute(
        "SELECT o.id, o.created_at, o.broker, o.ticker, o.action, o.qty, o.price, o.status, "
        "o.message, o.signal_id, "        # 체결가 파싱·근거 조회에 필요
        "COALESCE(s.source, '') src FROM orders o "
        "LEFT JOIN signals s ON s.id = o.signal_id "
        "WHERE o.notified IS NULL "
        "AND o.created_at >= replace(datetime('now','localtime','-1 day'),' ','T') ORDER BY o.id").fetchall()
    if not rows:
        return 0
    ids = [r["id"] for r in rows]
    groups: dict = {}
    for r in rows:
        groups.setdefault((r["src"] or "manual", r["action"]), []).append(r)

    sent = 0
    try:
        from src import notify

        for (src, action), items in groups.items():
            head = STRAT.get(src, f"📌 {src}")
            verb = "매수" if action == "buy" else "매도"
            L = [f"<b>{head} {verb} {len(items)}건</b>", ""]
            total = 0.0
            for r in items:
                mark = "✅" if r["status"] in OK else "❌"
                kr = str(r["ticker"]).isdigit()
                name = _name(con, r["ticker"]) if kr else r["ticker"]
                fq, fp = _fill(r["message"])                 # 실제 체결 수량·단가
                qty = fq if fq is not None else r["qty"]
                price = fp if fp is not None else r["price"]
                amt = (qty * price) if (qty and price) else None
                if amt and r["status"] in OK:
                    total += amt
                line = f"{mark} <b>{name}</b>"
                if kr and name != r["ticker"]:
                    line += f" ({r['ticker']})"
                if qty:
                    line += f" · {qty:,.4g}주"
                if price:
                    line += f" @ {_cur(price, kr)}"
                if amt:
                    line += f" = <b>{_cur(amt, kr)}</b>"
                L.append(line)
                # 매도면 손익, 매수면 전략 근거를 한 줄 덧붙임
                extra = _sell_pnl(con, r) if action == "sell" else _buy_reason(con, r)
                if extra:
                    L.append(f"   ↳ {extra}")
                if r["status"] not in OK:
                    L.append(f"   ⚠ {r['status']}: {str(r['message'] or '')[:60]}")
            if total:
                L += ["", f"합계 {_cur(total, str(items[0]['ticker']).isdigit())}"]
            notify.send("\n".join(L))
            sent += 1
    except Exception as e:
        from src.errlog import swallow

        swallow("trade_alerts.notify", e)                                           # 발송 실패해도 마킹은 진행(무한 재시도 방지)
    con.executemany("UPDATE orders SET notified=1 WHERE id=?", [(i,) for i in ids])
    con.commit()
    return sent


if __name__ == "__main__":
    import sys

    from dotenv import load_dotenv

    load_dotenv()
    sys.path.insert(0, ".")
    from src import db

    c = db.connect()
    print("발송:", notify_new_orders(c), "그룹")
    c.close()
