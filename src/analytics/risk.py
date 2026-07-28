"""리스크 측정 — 사후 MDD가 아니라 '앞으로 얼마까지 빠질 수 있나'를 확률로.

배경(reality_check): 모멘텀은 MDD -32~46%를 감내하는 대가로 프리미엄을 준다.
문제는 대부분이 그 낙폭에서 손절하고 나간다는 것 → **미리 숫자로 알고 시작**해야 버틴다.

- VaR(95/99%): "정상적인 하루 중 최악 5%/1%에서 얼마 잃나"
- CVaR(=기대손실): "그 최악 구간에 실제로 들어갔을 때 평균 얼마 잃나" (VaR보다 정직)
- 몬테카를로: 과거 수익 분포를 부트스트랩해 N개 미래 경로 → 낙폭 분포·파산확률
- 켈리 비율: 승률·손익비로 본 이론적 최적 베팅 (실전은 1/4~1/2 켈리 권장)

주의: VaR은 '정상 시장' 가정이라 진짜 위기(2008·2020)는 과소평가한다.
      그래서 CVaR·MC 최악경로를 함께 본다.
"""
import numpy as np


def var_cvar(returns, level: float = 0.95) -> dict:
    """히스토리컬 VaR/CVaR (일별 수익률 소수 배열). 반환값은 손실을 양수로."""
    r = np.asarray([x for x in returns if x is not None and not np.isnan(x)], dtype=float)
    if len(r) < 20:
        return {"n": len(r), "error": "표본 부족 (최소 20일)"}
    q = np.quantile(r, 1 - level)                 # 하위 (1-level) 분위 = VaR 경계
    tail = r[r <= q]
    return {
        "n": len(r),
        "level": level,
        "var_pct": round(-q * 100, 2),                          # 일간 VaR (%, 손실 양수)
        "cvar_pct": round(-tail.mean() * 100, 2) if len(tail) else None,
        "worst_pct": round(-r.min() * 100, 2),
        "vol_ann": round(r.std() * np.sqrt(252) * 100, 1),      # 연율 변동성
    }


def monte_carlo(returns, days: int = 252, sims: int = 2000, seed: int = 42) -> dict:
    """부트스트랩 몬테카를로 — 미래 days일 경로 sims개. 낙폭 분포·손실확률."""
    r = np.asarray([x for x in returns if x is not None and not np.isnan(x)], dtype=float)
    if len(r) < 30:
        return {"n": len(r), "error": "표본 부족 (최소 30일)"}
    rng = np.random.RandomState(seed)
    draws = rng.choice(r, size=(sims, days), replace=True)      # 과거 분포에서 복원추출
    paths = np.cumprod(1 + draws, axis=1)
    finals = paths[:, -1] - 1
    # 경로별 최대낙폭
    run_max = np.maximum.accumulate(paths, axis=1)
    mdds = (paths / run_max - 1).min(axis=1)
    return {
        "n": len(r), "days": days, "sims": sims,
        "ret_median": round(float(np.median(finals)) * 100, 1),
        "ret_p5": round(float(np.quantile(finals, 0.05)) * 100, 1),      # 하위 5% 시나리오
        "ret_p95": round(float(np.quantile(finals, 0.95)) * 100, 1),
        "mdd_median": round(float(np.median(mdds)) * 100, 1),
        "mdd_p95": round(float(np.quantile(mdds, 0.05)) * 100, 1),       # 최악 5% 낙폭
        "prob_loss": round(float((finals < 0).mean()) * 100, 1),         # 1년 후 손실 확률
        "prob_dd20": round(float((mdds <= -0.20).mean()) * 100, 1),      # -20% 낙폭 경험 확률
        "prob_dd40": round(float((mdds <= -0.40).mean()) * 100, 1),      # -40% 낙폭 경험 확률
    }


def kelly(win_rate: float, avg_win: float, avg_loss: float) -> dict:
    """켈리 기준. avg_win/avg_loss는 양수 크기(예: 0.08 = 8%)."""
    if avg_loss <= 0 or not (0 < win_rate < 1):
        return {"error": "입력 범위 오류"}
    b = avg_win / avg_loss                                  # 손익비
    f = (win_rate * b - (1 - win_rate)) / b                 # 최적 베팅 비율
    return {
        "payoff_ratio": round(b, 2),
        "kelly_pct": round(f * 100, 1),
        "half_kelly_pct": round(f * 50, 1),                 # 실전 권장 (변동성 절반)
        "quarter_kelly_pct": round(f * 25, 1),
        "edge": round((win_rate * avg_win - (1 - win_rate) * avg_loss) * 100, 2),
    }


def strategy_risk(con, strategy: str) -> dict | None:
    """가상장부 전략의 리스크 요약 (일별 에쿼티 → VaR/CVaR/MC)."""
    rows = con.execute("SELECT date, equity FROM daytrade_equity WHERE strategy=? "
                       "ORDER BY date", (strategy,)).fetchall()
    if len(rows) < 21:
        return {"strategy": strategy, "n": len(rows), "error": "표본 부족 (최소 21일)"}
    rets = [rows[i]["equity"] / rows[i - 1]["equity"] - 1 for i in range(1, len(rows))]
    out = {"strategy": strategy, "period": f"{rows[0]['date']}~{rows[-1]['date']}"}
    out.update(var_cvar(rets))
    out["mc"] = monte_carlo(rets)
    # 청산 기록으로 켈리
    tr = con.execute("SELECT pnl_pct FROM daytrade_ledger WHERE strategy=? AND status='closed'",
                     (strategy,)).fetchall()
    pnls = [t["pnl_pct"] for t in tr if t["pnl_pct"] is not None]
    if len(pnls) >= 10:
        w = [p for p in pnls if p > 0]
        l = [-p for p in pnls if p <= 0]
        if w and l:
            out["kelly"] = kelly(len(w) / len(pnls), np.mean(w) / 100, np.mean(l) / 100)
    return out


if __name__ == "__main__":
    import sys

    from dotenv import load_dotenv

    load_dotenv()
    sys.path.insert(0, ".")
    from src import db

    c = db.connect()
    print("=== 실계좌 리스크 (알파카 에쿼티 스냅샷) ===")
    # 주말 행(직전 영업일 값 복사 = 수익률 0%)을 빼야 VaR가 실제보다 작게 나오지 않는다
    from src.trading.portfolio import equity_curve

    rows = equity_curve(c, "alpaca")
    if len(rows) >= 21:
        rets = [rows[i][1] / rows[i - 1][1] - 1 for i in range(1, len(rows))]
        v = var_cvar(rets)
        print(f"  VaR95 {v['var_pct']}% · CVaR {v['cvar_pct']}% · 연변동성 {v['vol_ann']}%")
    else:
        print(f"  표본 {len(rows)}일 — 21일 필요")
    print("\n=== 가상장부 전략 리스크 ===")
    for s in ("momentum", "meanrev"):
        r = strategy_risk(c, s)
        if r and "error" in r:
            print(f"  {s}: {r['error']}")
        elif r:
            print(f"  {s}: VaR95 {r['var_pct']}% · CVaR {r['cvar_pct']}% · "
                  f"1년 MDD 중앙 {r['mc']['mdd_median']}% (최악5% {r['mc']['mdd_p95']}%)")
    c.close()
