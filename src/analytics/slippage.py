"""슬리피지·거래비용 실측 — 실전 전환의 최대 미지수.

배경(reality_check): 백테스트는 편도 5bp를 가정했는데, **편도 100bp면 모멘텀 알파가 소멸**한다.
모의 계좌는 즉시 체결이라 이 비용이 안 보인다. 실제로 얼마인지 재야 실전 판단이 가능하다.

**벤치마크 선택이 측정의 전부다.** 첫 구현은 '전일 종가' 기준이었는데 이는 틀렸다:
청산은 장중 실시간 트리거라 전일 종가와 비교하면 **갭·다일 변동이 전부 슬리피지로 잡힌다**.
실측 사례(2026-07-27): SK이터닉스 전일 80,900 → 체결 56,800 = -29.8%로 나왔으나, 이는
그날 주가가 실제로 -29.4% 폭락한 것이고 당일 종가(57,100) 대비 실제 체결 편차는 -0.5%였다.

그래서 벤치마크 2종만 쓴다:
1. **emit** — 주문 emit 시점 ref_price → 체결가. **순수 슬리피지**(2026-07-27부터 기록 시작).
2. **당일종가(close)** — 체결가 vs 같은 날 종가. 소급 가능한 표준 벤치마크로, 갭 오염이 없다.
   ("그날 가격대 대비 얼마나 잘/못 샀나" — 기관이 쓰는 close benchmark와 동일한 개념)

부호 규약: **양수 = 불리**(매수는 비싸게 샀고, 매도는 싸게 팔았다).
세금·수수료: KR 매도 거래세 0.18% + 수수료 추정을 별도 합산해 '총비용'을 낸다.
"""
import re

KR_SELL_TAX = 0.18          # 거래세(매도) %
KR_FEE = 0.015              # 위탁수수료 추정 편도 % (증권사별 상이)
US_FEE = 0.0                # Alpaca 무료 (SEC 수수료는 미미)
OUTLIER_PCT = 15.0          # |편차| 이 이상은 측정 오류로 간주해 격리 (하한가도 -30%가 한계)


def ensure(con):
    """signals.ref_price — emit 시점 기준가 (없으면 추가)."""
    try:
        con.execute("ALTER TABLE signals ADD COLUMN ref_price REAL")
        con.commit()
    except Exception:
        pass


def emit_ref(con, ticker: str, live=None):
    """emit 시점 기준가 — 실시간가가 있으면 그것, 없으면 최근 종가(EOD 신호의 결정가).

    **절대 예외를 던지지 않는다** — 측정용 부가 정보가 주문 emit을 막으면 안 된다.
    """
    try:
        if live and float(live) > 0:
            return float(live)
    except (TypeError, ValueError):
        pass
    try:
        r = con.execute("SELECT close FROM prices_daily WHERE symbol=? AND close IS NOT NULL "
                        "ORDER BY date DESC LIMIT 1", (ticker,)).fetchone()
        return r["close"] if r else None
    except Exception:
        return None


def _fill(msg):
    """orders.message에서 (수량, 체결가) 파싱 — 'filled 8@208000'.

    지수표기(1.392e+06)까지 받는다. 과거 :g 포맷 버그로 남은 행이 있어 필수
    (없으면 1.392원으로 읽혀 -99.98% 이상치가 됨).
    """
    num = r"\d+(?:\.\d+)?(?:[eE][+-]?\d+)?"
    m = re.search(rf"filled\s+({num})\s*@\s*({num})", str(msg or ""))
    if m:
        try:
            return float(m.group(1)), float(m.group(2))
        except ValueError:
            pass
    return None, None


def _same_day_close(con, ticker: str, order_date: str):
    """체결 당일 종가 — 갭 오염이 없는 소급 벤치마크."""
    r = con.execute(
        "SELECT close FROM prices_daily WHERE symbol=? AND date=? AND close IS NOT NULL",
        (ticker, order_date[:10])).fetchone()
    return r["close"] if r else None


def analyze(con, since: str = "2026-07-23") -> dict:
    """체결 주문의 슬리피지·총비용 실측. 반환: {trades, excluded, summary}"""
    ensure(con)
    rows = con.execute(
        "SELECT o.id, o.created_at, o.ticker, o.action, o.qty, o.status, o.message, "
        "s.source, s.ref_price FROM orders o LEFT JOIN signals s ON s.id=o.signal_id "
        "WHERE o.created_at >= ? AND o.status='filled' ORDER BY o.id", (since,)).fetchall()
    trades, excluded = [], []
    for r in rows:
        fq, fp = _fill(r["message"])
        if not fp:
            excluded.append({"ticker": r["ticker"], "why": "체결가 파싱 불가",
                             "msg": (r["message"] or "")[:40]})
            continue
        # 기준가: emit 시 기록한 ref_price(순수 슬리피지) > 당일 종가(소급 근사)
        ref, how = r["ref_price"], "emit"
        if not ref:
            ref, how = _same_day_close(con, r["ticker"], r["created_at"]), "당일종가"
        if not ref:
            excluded.append({"ticker": r["ticker"], "why": "당일 종가 미수집",
                             "msg": r["created_at"][:10]})
            continue
        kr = str(r["ticker"]).isdigit()
        sign = 1 if r["action"] == "buy" else -1
        slip = (fp / ref - 1) * 100 * sign          # 양수 = 불리
        if abs(slip) > OUTLIER_PCT:                 # 데이터 오류 격리 (측정치 오염 방지)
            excluded.append({"ticker": r["ticker"], "why": f"이상치 {slip:+.1f}%",
                             "msg": f"기준 {ref:,.0f} → 체결 {fp:,.0f}"})
            continue
        tax = KR_SELL_TAX if (kr and r["action"] == "sell") else 0.0
        fee = KR_FEE if kr else US_FEE
        trades.append({
            "date": r["created_at"][:10], "ticker": r["ticker"], "action": r["action"],
            "ref": ref, "fill": fp, "qty": fq, "kr": kr,
            "slip": slip, "tax": tax, "fee": fee, "total": slip + tax + fee,
            "src": r["source"] or "manual", "amount": (fq or 0) * fp, "how": how,
        })
    if not trades:
        return {"trades": [], "excluded": excluded, "summary": None}

    def _avg(xs, key):
        return sum(x[key] for x in xs) / len(xs) if xs else 0.0

    def _wavg(xs, key):                             # 금액가중 (실제 비용 체감)
        tot = sum(x["amount"] for x in xs)
        return sum(x[key] * x["amount"] for x in xs) / tot if tot else 0.0

    emits = [t for t in trades if t["how"] == "emit"]
    buys = [t for t in trades if t["action"] == "buy"]
    sells = [t for t in trades if t["action"] == "sell"]
    krt = [t for t in trades if t["kr"]]
    ust = [t for t in trades if not t["kr"]]
    return {
        "trades": trades, "excluded": excluded,
        "summary": {
            "n": len(trades), "n_buy": len(buys), "n_sell": len(sells),
            "n_excl": len(excluded),
            "slip_avg": _avg(trades, "slip"), "slip_wavg": _wavg(trades, "slip"),
            "total_avg": _avg(trades, "total"), "total_wavg": _wavg(trades, "total"),
            "buy_slip": _avg(buys, "slip") if buys else None,
            "sell_slip": _avg(sells, "slip") if sells else None,
            "kr_total": _avg(krt, "total") if krt else None,
            "us_total": _avg(ust, "total") if ust else None,
            "worst": max(trades, key=lambda t: t["total"]),
            "best": min(trades, key=lambda t: t["total"]),
            "amount": sum(t["amount"] for t in trades),
            "n_emit": sum(1 for t in trades if t["how"] == "emit"),
            # **emit 기준만 따로** — 판정은 이 값으로 한다.
            # 당일종가 기준은 장중 드리프트가 섞여 슬리피지가 아니다. 특히 2026-07-29처럼
            # 코스피가 -10.84% 빠진 날엔 "종가보다 싸게 팔았다"가 실행품질이 아니라
            # 그냥 하락장이라는 뜻이 된다(실측: 혼합 -227bp vs emit-only는 별개 값).
            "emit_wavg": _wavg(emits, "total") if emits else None,
            "emit_avg": _avg(emits, "total") if emits else None,
            "emit_amount": sum(t["amount"] for t in emits),
        },
    }


ROUNDTRIP_KR = KR_SELL_TAX + KR_FEE * 2   # KR 왕복 확정비용 = 0.21% (21bp)


def verdict(summary) -> str:
    """백테스트 가정(편도 5bp) 대비 판정 — 실전 전환 가부의 핵심 근거.

    **emit 표본이 없으면 판정하지 않는다.** 당일종가 기준은 갭 오염은 없지만 장중 드리프트가
    남는다: 우리는 상승 중인 모멘텀 종목을 장중에 사므로 체결가가 당일 종가보다 체계적으로
    낮게 나와(실측 -296bp) '비용이 음수'라는 비현실적 결과가 된다. 이걸 판정에 쓰면 안 된다.
    """
    if not summary:
        return "표본 없음"
    if not summary["n_emit"]:
        return (f"판정 보류 — emit 기준 표본 0건 (당일종가 기준 {summary['total_wavg']*100:+.0f}bp는 "
                f"장중 드리프트 포함이라 슬리피지 아님). 확정비용만 KR 왕복 "
                f"{ROUNDTRIP_KR*100:.0f}bp")
    # **emit 표본만** 쓴다. 원래 n_emit 유무만 확인하고 정작 숫자는 전체 혼합값을 썼다 —
    # docstring이 경고한 함정에 코드가 그대로 빠져 있었다(2026-07-29 발견).
    bp = summary["emit_wavg"] * 100           # % → bp
    if bp <= 20:
        return f"양호 ({bp:.0f}bp) — 백테스트 가정(5bp) 대비 여유, 알파 유지 가능"
    if bp <= 50:
        return f"보통 ({bp:.0f}bp) — 알파 일부 잠식(reality_check: 50bp면 CAGR 28.9→21.5%)"
    if bp <= 100:
        return f"주의 ({bp:.0f}bp) — 알파 절반 이상 잠식, 회전율 낮춰야"
    return f"위험 ({bp:.0f}bp) — 100bp 초과 시 모멘텀 알파 소멸(실전 재검토 필요)"


if __name__ == "__main__":
    import sys

    from dotenv import load_dotenv

    load_dotenv()
    sys.path.insert(0, ".")
    from src import db

    c = db.connect()
    r = analyze(c)
    s = r["summary"]
    if not s:
        print("체결 표본 없음")
        for e in r["excluded"]:
            print(f"  제외 {e['ticker']}: {e['why']} ({e['msg']})")
        sys.exit()
    print(f"=== 슬리피지 실측 ({s['n']}건 · 매수 {s['n_buy']} / 매도 {s['n_sell']}) ===")
    print(f"  기준가: emit {s['n_emit']}건 / 당일종가 {s['n'] - s['n_emit']}건"
          f" · 제외 {s['n_excl']}건")
    print(f"  총 거래대금: {s['amount']:,.0f}")
    print()
    tag = "" if s["n_emit"] else "   ← 장중드리프트 포함(참고치)"
    print(f"  체결편차 (단순평균) : {s['slip_avg']:+.3f}%  ({s['slip_avg']*100:+.0f}bp)")
    print(f"  체결편차 (금액가중) : {s['slip_wavg']:+.3f}%  ({s['slip_wavg']*100:+.0f}bp){tag}")
    print(f"  + 세금·수수료 포함  : {s['total_wavg']:+.3f}%  ({s['total_wavg']*100:+.0f}bp)"
          f"   ← 두 기준 혼합, 판정에 쓰지 않음")
    if s.get("emit_wavg") is not None:
        print(f"  ▶ emit 기준만       : {s['emit_wavg']:+.3f}%  ({s['emit_wavg']*100:+.0f}bp)"
              f"  · {s['n_emit']}건 / {s['emit_amount']:,.0f}  ← **판정 근거**")
    print(f"  확정비용(KR 왕복)   : {ROUNDTRIP_KR:+.3f}%  ({ROUNDTRIP_KR*100:+.0f}bp) — 세금·수수료만")
    print()
    if s["buy_slip"] is not None:
        print(f"  매수 슬리피지: {s['buy_slip']:+.3f}%")
    if s["sell_slip"] is not None:
        print(f"  매도 슬리피지: {s['sell_slip']:+.3f}%")
    if s["kr_total"] is not None:
        print(f"  KR 총비용: {s['kr_total']:+.3f}% (거래세 0.18% 포함)")
    if s["us_total"] is not None:
        print(f"  US 총비용: {s['us_total']:+.3f}%")
    print()
    for lab, t in (("최악", s["worst"]), ("최선", s["best"])):
        print(f"  {lab}: {t['ticker']} {t['action']} {t['total']:+.2f}% "
              f"(기준 {t['ref']:,.0f} → 체결 {t['fill']:,.0f}, {t['how']})")
    if r["excluded"]:
        print()
        print("  [제외된 건]")
        for e in r["excluded"]:
            print(f"   · {e['ticker']}: {e['why']} — {e['msg']}")
    print()
    print("판정:", verdict(s))
    print("\n※ 양수 = 불리(매수는 비싸게, 매도는 싸게). 당일종가 기준은 갭 오염 없음")
    c.close()
