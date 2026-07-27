"""수익 귀속 분해 — '이 수익이 실력인가 시장 덕인가'.

2주 검증(~08-06) 판정의 핵심 도구. 지금은 '로테이션 +4%'만 보이는데, 그게
시장이 올라서인지(베타) 종목을 잘 골라서인지(알파) 구분이 안 된다.

분해 축 2개:
1) **알파/베타** (CAPM 회귀): r_p = α + β·r_m + ε
   - β = 시장 민감도 (1.2면 시장이 1% 오를 때 1.2% 오름 — 그만큼은 실력 아님)
   - α = 시장으로 설명 안 되는 초과수익 = **진짜 실력** (연율화)
   - R² = 시장이 설명하는 비율 (높을수록 '그냥 시장 따라간 것')
2) **Brinson 귀속** (섹터별): 총초과수익 = 배분효과 + 선택효과
   - 배분(allocation): 좋은 섹터에 많이 담았나 (Σ(wp-wb)·rb)
   - 선택(selection): 그 섹터 안에서 좋은 종목을 골랐나 (Σ wb·(rp-rb))

표본이 짧으면(2주=10영업일) 통계적 유의성이 없다 — n을 함께 보고 판단할 것.
"""
import numpy as np


def alpha_beta(port_rets, mkt_rets, freq: int = 252) -> dict:
    """CAPM 회귀 → {alpha_ann, beta, r2, n, t_alpha}. 수익률은 일별 소수(0.01=1%)."""
    p = np.asarray(port_rets, dtype=float)
    m = np.asarray(mkt_rets, dtype=float)
    ok = ~(np.isnan(p) | np.isnan(m))
    p, m = p[ok], m[ok]
    n = len(p)
    if n < 5:
        return {"n": n, "error": "표본 부족 (최소 5일)"}
    beta, alpha = np.polyfit(m, p, 1)          # p = beta*m + alpha
    resid = p - (beta * m + alpha)
    ss_tot = ((p - p.mean()) ** 2).sum()
    r2 = 1 - (resid ** 2).sum() / ss_tot if ss_tot else 0.0
    # alpha의 t값 (잔차 표준오차 기반)
    se = resid.std(ddof=2) / np.sqrt(n) if n > 2 else np.nan
    t_alpha = alpha / se if se and not np.isnan(se) else np.nan
    return {
        "n": n,
        "beta": round(float(beta), 3),
        "alpha_daily": float(alpha),
        "alpha_ann": round(float(alpha) * freq * 100, 2),      # 연율화 %
        "r2": round(float(r2), 3),
        "t_alpha": round(float(t_alpha), 2) if not np.isnan(t_alpha) else None,
        "market_share": round(float(r2) * 100, 1),             # 시장이 설명한 %
    }


def brinson(port_w: dict, port_r: dict, bench_w: dict, bench_r: dict) -> dict:
    """섹터 Brinson 귀속. w=비중(합1), r=해당 기간 수익률(소수).

    반환: {allocation, selection, interaction, total, by_sector}
    - allocation: 섹터 배분 결정의 기여 (좋은 섹터를 많이 담았나)
    - selection : 섹터 내 종목선택 기여 (같은 섹터에서 더 좋은 종목을 골랐나)
    """
    secs = set(port_w) | set(bench_w)
    br_total = sum(bench_w.get(s, 0) * bench_r.get(s, 0) for s in secs)
    alloc = sel = inter = 0.0
    by = {}
    for s in secs:
        wp, wb = port_w.get(s, 0.0), bench_w.get(s, 0.0)
        rp, rb = port_r.get(s, 0.0), bench_r.get(s, 0.0)
        a = (wp - wb) * (rb - br_total)        # 배분 (벤치 총수익 기준)
        sl = wb * (rp - rb)                    # 선택
        it = (wp - wb) * (rp - rb)             # 상호작용
        alloc += a; sel += sl; inter += it
        by[s] = {"alloc": round(a * 100, 3), "sel": round(sl * 100, 3),
                 "total": round((a + sl + it) * 100, 3),
                 "wp": round(wp * 100, 1), "wb": round(wb * 100, 1)}
    return {
        "allocation": round(alloc * 100, 3),
        "selection": round(sel * 100, 3),
        "interaction": round(inter * 100, 3),
        "total": round((alloc + sel + inter) * 100, 3),
        "by_sector": dict(sorted(by.items(), key=lambda kv: -abs(kv[1]["total"]))),
    }


def _daily_returns(con, symbol: str, market: str | None = None, days: int = 90):
    q = "SELECT date, close FROM prices_daily WHERE symbol=?"
    args = [symbol]
    if market:
        q += " AND market=?"
        args.append(market)
    rows = con.execute(q + " ORDER BY date DESC LIMIT ?", (*args, days + 1)).fetchall()
    rows = rows[::-1]
    return ([r["date"] for r in rows[1:]],
            [rows[i]["close"] / rows[i - 1]["close"] - 1 for i in range(1, len(rows))])


def attribute_strategy(con, strategy: str, bench: str = "SPY") -> dict | None:
    """가상장부 전략(momentum/meanrev)의 알파·베타 — daytrade_equity 기반."""
    rows = con.execute("SELECT date, equity FROM daytrade_equity WHERE strategy=? "
                       "ORDER BY date", (strategy,)).fetchall()
    if len(rows) < 6:
        return {"strategy": strategy, "n": len(rows), "error": "표본 부족 (최소 6일)"}
    dates = [r["date"] for r in rows]
    prets = [rows[i]["equity"] / rows[i - 1]["equity"] - 1 for i in range(1, len(rows))]
    bmap = {}
    for r in con.execute("SELECT date, close FROM prices_daily WHERE symbol=? ORDER BY date",
                         (bench,)):
        bmap[r["date"]] = r["close"]
    bd = sorted(bmap)
    brets = []
    for i in range(1, len(dates)):
        prev = [d for d in bd if d <= dates[i - 1]]
        cur = [d for d in bd if d <= dates[i]]
        brets.append(bmap[cur[-1]] / bmap[prev[-1]] - 1 if prev and cur else np.nan)
    out = alpha_beta(prets, brets)
    out["strategy"] = strategy
    out["period"] = f"{dates[0]}~{dates[-1]}"
    return out


if __name__ == "__main__":
    import sys

    from dotenv import load_dotenv

    load_dotenv()
    sys.path.insert(0, ".")
    from src import db

    c = db.connect()
    print("=== 가상장부 전략 알파/베타 ===")
    for s in ("momentum", "meanrev"):
        r = attribute_strategy(c, s)
        if r and "error" in r:
            print(f"  {s}: {r['error']} (n={r.get('n')})")
        elif r:
            print(f"  {s} ({r['period']}): α {r['alpha_ann']:+.1f}%/년 · β {r['beta']} · "
                  f"R² {r['r2']} (시장설명 {r['market_share']}%) · t(α) {r['t_alpha']} · n={r['n']}")
    c.close()
