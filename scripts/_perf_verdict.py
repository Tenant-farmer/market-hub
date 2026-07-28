"""2차 판정 — **백테스트 예측이 실거동에서 재현되는가**.

왜 '수익률 판정'이 아닌가: 20영업일로 CAGR·Sharpe·α의 t값을 논하는 건 자기기만이다.
reality_check가 이미 1년 -20% 낙폭 확률을 30.6%로 냈다 — 1년도 짧은데 20일은 말할 것도 없다.
20일로 **판정할 수 있는 것**은 수준(level)이 아니라 **방향(direction)과 임계(threshold)**다.

그래서 사전에 등록한 예측을 실측과 대조한다(research-discipline의 사전등록 원칙).
예측은 전부 백테스트·검증에서 이미 나온 숫자이고, 근거 스크립트를 함께 적어 추적 가능하게 한다.

판정 종류:
- direction : 부호·대소만 본다. 20일이면 유의미 (예: KR 로테이션이 US보다 나쁠 것)
- threshold : 선을 넘었나만 본다 (예: 편도 비용 100bp 초과면 알파 소멸)
- level     : 수치 자체를 맞혀야 한다 → **표본 부족 시 판정하지 않고 참고치로만 표기**

호출: scripts/verdict.py 가 import 해서 씀. 단독 실행도 가능(python scripts/_perf_verdict.py)
"""
MIN_DIRECTION_DAYS = 20        # 방향 판정 최소 표본
MIN_LEVEL_DAYS = 60            # 수준 판정 최소 표본 (사실상 2차 이후 과제)


def _eq_days(con) -> int:
    return con.execute("SELECT COUNT(DISTINCT date) n FROM portfolio_snapshots").fetchone()["n"]


def _virtual_ab(con):
    """가상장부 A/B — 백테스트 예측: 모멘텀 +12153% vs 단타 +349%."""
    eqs = {}
    for s in ("momentum", "meanrev"):
        r = con.execute("SELECT equity FROM daytrade_equity WHERE strategy=? "
                        "ORDER BY date DESC LIMIT 1", (s,)).fetchone()
        if r:
            eqs[s] = (r["equity"] / 100000 - 1) * 100
    if len(eqs) < 2:
        return None, "가상장부 미가동"
    m, v = eqs["momentum"], eqs["meanrev"]
    return m >= v, f"모멘텀 {m:+.2f}% vs 단타 {v:+.2f}%"


def _rotation_us_vs_kr(con):
    """로테이션 US vs KR — 백테스트: US +6042% / KR -61~-74%(실패).

    KR을 굳이 돌리는 이유가 '검증 체계 자체의 검증'이다. KR이 US보다 나쁘게 나와야
    백테스트의 예측력이 실거동에서 확인된다. 반대로 나오면 백테스트를 의심해야 한다.
    """
    out = {}
    for mkt, cond in (("US", "NOT GLOB '[0-9]*'"), ("KR", "GLOB '[0-9]*'")):
        r = con.execute(
            f"SELECT AVG((s.entry_px - 0) * 0) x, COUNT(*) n FROM rotation_slots s "
            f"WHERE s.symbol {cond}").fetchone()
        out[mkt] = r["n"]
    # 슬롯 수익률은 브로커 평가손익이 필요 → 여기선 슬롯 유지 여부만. 실손익은 실계좌 조회
    try:
        from src.trading.brokers import kiwoom
        from src.trading.brokers import alpaca

        rot = {r["symbol"] for r in con.execute("SELECT symbol FROM rotation_slots")}
        pl = {"US": [], "KR": []}
        if kiwoom.configured():
            b = kiwoom.KiwoomBroker().account_balance()
            for h in (b or {}).get("holdings", []):
                if h["code"] in rot:
                    pl["KR"].append(h["plpc"])
        if alpaca.configured():
            for p in alpaca.AlpacaBroker().get_positions():
                if p["symbol"] in rot:
                    pl["US"].append(float(p.get("unrealized_plpc", 0) or 0) * 100)
        if not pl["US"] or not pl["KR"]:
            return None, f"슬롯 US {out['US']} · KR {out['KR']} — 한쪽 미보유로 대조 불가"
        us, kr = sum(pl["US"]) / len(pl["US"]), sum(pl["KR"]) / len(pl["KR"])
        return us >= kr, (f"US 평균 {us:+.2f}% ({len(pl['US'])}종목) vs "
                          f"KR {kr:+.2f}% ({len(pl['KR'])}종목)")
    except Exception as e:
        return None, f"조회 실패: {type(e).__name__}"


def _slippage(con):
    """거래비용 — reality_check: 편도 100bp 넘으면 모멘텀 알파 소멸."""
    try:
        from src.analytics.slippage import analyze

        r = analyze(con)
        s = r["summary"]
        if not s:
            return None, "체결 표본 없음"
        # **emit 기준 건만 골라서 재계산**한다. summary의 가중평균은 당일종가 기준 건까지
        # 섞여 있어(장중 드리프트 포함) 그대로 쓰면 판정이 오염된다
        emit = [t for t in r["trades"] if t["how"] == "emit"]
        if not emit:
            return None, (f"emit 기준 0건 — 당일종가 기준 {s['total_wavg']*100:+.0f}bp는 "
                          f"장중 드리프트 포함이라 판정 불가")
        amt = sum(t["amount"] for t in emit)
        bp = (sum(t["total"] * t["amount"] for t in emit) / amt * 100) if amt else 0.0
        return bp <= 100, f"{bp:+.0f}bp (emit {len(emit)}건 · 당일종가 혼합 제외)"
    except Exception as e:
        return None, f"측정 실패: {type(e).__name__}"


def _stop_rate(con, since: str):
    """손절 발동 빈도 — stop_loss_sweep(-15%): US 연 9.3회 예상.

    넓힌 손절이 실제로 덜 걸리는지 본다. 너무 자주면 임계가 여전히 좁다는 뜻.
    """
    from datetime import date

    # 손절폭을 US -15%/KR -25%로 넓힌 게 2026-07-28. 그 이전 발동은 -8% 시절이라
    # 지금 기준의 빈도가 아니다 → **변경일 이후만** 센다
    WIDENED = "2026-07-28"
    base = max(since, WIDENED)
    n = con.execute(
        "SELECT COUNT(*) n FROM signals WHERE source='exit' AND strategy LIKE '청산:손절%' "
        "AND received_at >= ?", (base,)).fetchone()["n"]
    old = con.execute(
        "SELECT COUNT(*) n FROM signals WHERE source='exit' AND strategy LIKE '청산:손절%' "
        "AND received_at >= ? AND received_at < ?", (since, base)).fetchone()["n"]
    days = max(1, (date.today() - date.fromisoformat(base)).days)
    # 며칠치를 연 단위로 환산하면 '연 365회' 같은 헛숫자가 나온다 → 표본이 쌓여야 환산
    rate = f" = 연 {n * 365 / days:.0f}회 환산" if days >= 30 else " (연 환산은 30일↑부터)"
    return None, (f"넓힌 기준({WIDENED}~) {n}건 / {days}일{rate}"
                  f" · 이전 -8% 시절 {old}건 (백테스트 -15% 기준 연 9.3회)")


def _alpha_beta(con):
    """α/β — 20일로는 t값이 무의미. **참고치**로만 표기한다."""
    try:
        from src.analytics.attribution import attribute_strategy

        rows = []
        for s in ("momentum", "meanrev"):
            r = attribute_strategy(con, s)
            if r and "error" not in r:
                rows.append(f"{s} α {r['alpha_ann']:+.1f}%/년 β {r['beta']} t {r['t_alpha']}")
        return None, " · ".join(rows) if rows else "산출 불가"
    except Exception as e:
        return None, f"산출 실패: {type(e).__name__}"


# 사전 등록 예측표 — (키, 종류, 주장, 근거, 판정함수)
PREDICTIONS = [
    ("virtual_ab", "direction", "모멘텀이 단타보다 낫다",
     "백테스트 +12153% vs +349%", lambda con, since: _virtual_ab(con)),
    ("rotation", "direction", "US 로테이션이 KR보다 낫다",
     "leader_backtest US +6042% / KR -61~-74%", lambda con, since: _rotation_us_vs_kr(con)),
    ("slippage", "threshold", "편도 거래비용 ≤ 100bp",
     "reality_check: 100bp 초과 시 알파 소멸", lambda con, since: _slippage(con)),
    ("stop_rate", "level", "손절 발동 연 9.3회 수준",
     "stop_loss_sweep(-15%)", _stop_rate),
    ("alpha_beta", "level", "α > 0 (시장 초과수익)",
     "attribution", lambda con, since: _alpha_beta(con)),
]


def perf_verdict(con, since: str) -> list[dict]:
    """2차 판정 — 예측별 대조. 표본이 모자란 종류는 판정하지 않고 참고치로 남긴다."""
    n_eq = _eq_days(con)
    out = []
    for key, kind, claim, basis, fn in PREDICTIONS:
        need = MIN_DIRECTION_DAYS if kind in ("direction", "threshold") else MIN_LEVEL_DAYS
        try:
            ok, detail = fn(con, since)
        except Exception as e:
            ok, detail = None, f"평가 실패: {type(e).__name__}: {str(e)[:40]}"
        if n_eq < need:
            ok = None
            detail = f"{detail}  [표본 {n_eq}/{need}일 — 참고치]"
        out.append({"item": claim, "kind": kind, "basis": basis, "ok": ok, "detail": detail})
    return out


def render(rows: list[dict], n_eq: int) -> str:
    L = [f"에쿼티 표본 {n_eq}일 · 방향 판정 {MIN_DIRECTION_DAYS}일↑ / 수준 판정 {MIN_LEVEL_DAYS}일↑", ""]
    for r in rows:
        icon = {True: "✅", False: "❌", None: "⏳"}[r["ok"]]
        L.append(f"  {icon} [{r['kind']:<9}] {r['item']}")
        L.append(f"      근거 {r['basis']}")
        L.append(f"      실측 {r['detail']}")
    judged = [r for r in rows if r["ok"] is not None]
    if not judged:
        L += ["", "  → **판정 보류** — 표본 부족. 지금은 관찰만 (무리한 결론 방지)"]
    else:
        bad = [r for r in judged if not r["ok"]]
        L += ["", f"  → 판정 가능 {len(judged)}/{len(rows)}항목 · "
                  + ("전부 예측대로" if not bad
                     else f"**예측 빗나감 {len(bad)}건**: " + ", ".join(r["item"] for r in bad))]
    return "\n".join(L)


if __name__ == "__main__":
    import sys
    from pathlib import Path

    ROOT = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(ROOT))
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    from src import db

    c = db.connect()
    print("=== 2차 판정 (전략 성과 — 예측 대조) ===")
    print(render(perf_verdict(c, "2026-07-23"), _eq_days(c)))
    c.close()
