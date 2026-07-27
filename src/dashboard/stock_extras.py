"""종목 상세 보강 — 검증된 것만 추가 (2026-07-27 스킬 검증 결과 반영).

기존 상세에 이미 있는 것(내부자·기관·EPS이력·애널리스트·재무6카드)은 중복하지 않는다.
없으면서 검증상 가치 있는 3가지만:

1. **PEAD 해석** — 유일하게 검증된 팩터(B1: Q5-Q1 63일 +12.27%p). EPS 숫자는 이미 있지만
   '이 서프라이즈가 어느 등급이고 통계적으로 뭘 기대하나'가 없다.
2. **DART 공시** (KR) — 수집 중인데 종목 상세에 미노출.
3. **종목 리스크** — 변동성·VaR·최대낙폭. A3 도구 재사용. 매수 전 '얼마나 흔들리나'.

무효 판정된 지표(캔들·RSI·밸류필터)는 넣지 않거나 경고와 함께 표시한다.
"""
import numpy as np


def pead_context(sym: str) -> dict | None:
    """최근 실적 서프라이즈 + PEAD 등급·기대치 (백테스트 1,797건 근거)."""
    try:
        import yfinance as yf

        h = yf.Ticker(sym).earnings_history
        if h is None or len(h) == 0:
            return None
        h = h.reset_index().dropna(subset=["epsActual", "epsEstimate"])
        if h.empty:
            return None
        last = h.iloc[-1]
        act, est = float(last["epsActual"]), float(last["epsEstimate"])
        if not est:
            return None
        s = (act - est) / abs(est)
        errs = (h["epsActual"] - h["epsEstimate"]).astype(float)
        sd = float(errs.std()) if len(errs) >= 3 else None
        sue = (act - est) / sd if sd else None
        recent = h.tail(4)
        beats = int(((recent["epsActual"] - recent["epsEstimate"]) > 0).sum())
        # 등급 (event_alerts와 동일 기준 — SUE 병행)
        if s >= 0.25 or (sue is not None and sue >= 2 and s > 0.02):
            grade, exp, cls = "대형 서프라이즈", "63일 평균 +9.4% (승률 59%)", "pos"
        elif s <= -0.04 or (sue is not None and sue <= -2):
            grade, exp, cls = "실적 미스", "63일 평균 -6.2% (승률 32%)", "neg"
        elif s > 0.02:
            grade, exp, cls = "소폭 beat", "63일 평균 +1% 내외", ""
        else:
            grade, exp, cls = "컨센서스 부합", "신호 약함 (63일 ~-1%)", ""
        hist = [{"date": str(r.get("quarter", r.get("index", "")))[:10],
                 "act": float(r["epsActual"]), "est": float(r["epsEstimate"]),
                 "sp": (float(r["epsActual"]) - float(r["epsEstimate"])) / abs(float(r["epsEstimate"]))
                 if r["epsEstimate"] else None}
                for _, r in h.tail(6).iterrows()]
        return {"grade": grade, "exp": exp, "cls": cls, "surprise": s * 100,
                "sue": sue, "beats": beats, "n_recent": len(recent),
                "act": act, "est": est, "hist": hist[::-1]}
    except Exception:
        return None


def risk_profile(con, symbol: str) -> dict | None:
    """종목 리스크 — 변동성·VaR·최대낙폭 (A3 도구 재사용)."""
    rows = con.execute(
        "SELECT close FROM prices_daily WHERE symbol=? ORDER BY date DESC LIMIT 252",
        (symbol,)).fetchall()
    if len(rows) < 60:
        return None
    px = np.array([r["close"] for r in rows][::-1], dtype=float)
    rets = px[1:] / px[:-1] - 1
    try:
        from src.analytics.risk import var_cvar

        v = var_cvar(rets)
        if "error" in v:
            return None
    except Exception:
        return None
    run_max = np.maximum.accumulate(px)
    mdd = float((px / run_max - 1).min()) * 100
    # SPY 대비 베타 (있으면)
    beta = None
    try:
        srows = con.execute(
            "SELECT close FROM prices_daily WHERE symbol='SPY' ORDER BY date DESC LIMIT 252"
        ).fetchall()
        if len(srows) >= len(rows):
            spx = np.array([r["close"] for r in srows][::-1][-len(px):], dtype=float)
            srets = spx[1:] / spx[:-1] - 1
            n = min(len(rets), len(srets))
            if n >= 60 and srets[-n:].std():
                beta = round(float(np.polyfit(srets[-n:], rets[-n:], 1)[0]), 2)
    except Exception:
        pass
    return {"var95": v["var_pct"], "cvar95": v["cvar_pct"], "vol_ann": v["vol_ann"],
            "worst": v["worst_pct"], "mdd_1y": round(mdd, 1), "beta": beta,
            "n": len(rets)}


def dart_filings(con, code: str, limit: int = 6) -> list[dict]:
    """KR 공시 이력 (DART 수집분 — 종목 상세엔 미노출이었음)."""
    try:
        rows = con.execute(
            "SELECT dt, title, url FROM news WHERE source='DART' AND code=? "
            "ORDER BY dt DESC LIMIT ?", (code, limit)).fetchall()
        return [{"date": r["dt"][:10], "title": r["title"].replace("📋 ", ""),
                 "url": r["url"]} for r in rows]
    except Exception:
        return []


def insider_kr_note(con, code: str) -> dict | None:
    """KR은 Form 4가 없으므로 DART 지분공시로 대용 (제목 키워드 매칭)."""
    try:
        rows = con.execute(
            "SELECT dt, title FROM news WHERE source='DART' AND code=? "
            "AND (title LIKE '%주식등의대량보유%' OR title LIKE '%임원ㆍ주요주주%') "
            "ORDER BY dt DESC LIMIT 3", (code,)).fetchall()
        if not rows:
            return None
        return {"n": len(rows), "latest": rows[0]["dt"][:10],
                "titles": [r["title"].replace("📋 ", "") for r in rows]}
    except Exception:
        return None
