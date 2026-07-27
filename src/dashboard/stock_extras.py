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


def financials_trend(sym: str, kr: bool = False) -> dict | None:
    """재무 3표 분기 추세 — 매출·영업이익·순이익·영업현금흐름 (참고용).

    ⚠ 검증 결과 펀더멘털은 매매 신호로 무효(B2 발생액 IC 0.002, A 퀄리티 기각).
    '추세를 눈으로 본다'는 참고 목적으로만 표시하며 UI에 그 사실을 명시한다.
    발생액(순이익-영업현금흐름)은 이익의 질 참고치로 함께 계산.
    """
    try:
        import yfinance as yf

        t = yf.Ticker(sym + (".KS" if kr else ""))
        fin, cf = t.quarterly_financials, t.quarterly_cashflow
        if fin is None or fin.empty:
            return None

        def _series(df, keys, n=6):
            for k in keys:
                if df is not None and not df.empty and k in df.index:
                    s = df.loc[k].dropna()
                    if len(s):
                        return [(str(d)[:7], float(v)) for d, v in list(s.items())[:n]][::-1]
            return []
        rev = _series(fin, ["Total Revenue", "Operating Revenue"])
        op = _series(fin, ["Operating Income", "EBIT"])
        ni = _series(fin, ["Net Income", "Net Income Common Stockholders"])
        cfo = _series(cf, ["Operating Cash Flow", "Total Cash From Operating Activities"])
        if not rev:
            return None
        # 이익의 질: 순이익 대비 영업현금흐름 (1.0 이상이면 현금이 실함)
        quality = None
        if ni and cfo and ni[-1][1]:
            quality = cfo[-1][1] / ni[-1][1]
        # YoY (4분기 전 대비)
        def _yoy(s):
            return (s[-1][1] / s[0][1] - 1) * 100 if len(s) >= 5 and s[0][1] else None
        return {"rev": rev, "op": op, "ni": ni, "cfo": cfo, "quality": quality,
                "rev_yoy": _yoy(rev), "op_yoy": _yoy(op), "ni_yoy": _yoy(ni),
                "cur": "원" if kr else "$"}
    except Exception:
        return None


def valuation_band(con, sym: str, kr: bool = False) -> dict | None:
    """밸류 밴드 — PER/PBR의 과거 범위 내 현재 위치 (참고용).

    ⚠ 검증 결과 밸류 팩터는 **역방향**(B3: 저PBR IC -0.095 — 싼 종목이 덜 오름).
    '지금 역사적으로 비싼가/싼가'의 맥락 참고용이며, 싸다고 사라는 신호가 아니다.
    데이터: yfinance 현재 PER/PBR + 주가 밴드로 과거 위치 근사(과거 PER 시계열은 무료 미제공).
    """
    try:
        import numpy as np
        import yfinance as yf

        info = yf.Ticker(sym + (".KS" if kr else "")).info
        pe, pb = info.get("trailingPE"), info.get("priceToBook")
        rows = con.execute(
            "SELECT close FROM prices_daily WHERE symbol=? ORDER BY date DESC LIMIT 1260",
            (sym,)).fetchall()
        if len(rows) < 252:
            return None
        px = np.array([r["close"] for r in rows][::-1], dtype=float)
        cur = px[-1]
        out = {"pe": pe, "pb": pb, "n_years": round(len(px) / 252, 1)}
        # 주가 밴드 내 위치 (PER 시계열 대용 — 이익 변화는 반영 못 하나 방향 참고)
        lo, hi = float(px.min()), float(px.max())
        out["px_pos"] = round((cur - lo) / (hi - lo) * 100, 1) if hi > lo else 50.0
        out["px_lo"], out["px_hi"], out["px_cur"] = lo, hi, float(cur)
        # 52주 기준 위치도 함께
        y = px[-252:]
        out["y_pos"] = round((cur - y.min()) / (y.max() - y.min()) * 100, 1) \
            if y.max() > y.min() else 50.0
        return out
    except Exception:
        return None


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
