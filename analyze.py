"""분석 CLI.

python analyze.py --us   # 미국 섹터 RS/로테이션/과열 계산 + 콘솔 요약
"""
import argparse

from src import config, db
from src.analytics import leaders, overheat, participation, relative_strength, rotation, store

QUAD_NAMES = {1: "Leading", 2: "Weakening", 3: "Lagging", 4: "Improving"}


def run_us():
    cfg = config.load()["us"]
    sectors = [s for s in cfg["symbols"] if s not in (cfg["benchmark"],)]
    bench = cfg["benchmark"]

    con = db.connect()
    relative_strength.compute(con, "us_sector", sectors, bench)
    rotation.compute(con, "us_sector", sectors, bench)
    overheat.compute(con, "us_sector", sectors + [bench])
    leaders.compute_sector(con, "us_sector")
    participation.compute(con, "us_sector", "US_STOCK",
                          anchor_date=store.latest_date(con, "us_sector", "rs_21"))
    n = leaders.compute_stocks(con)
    print(f"us_stock leader rows: {n}")

    # 콘솔 요약: 최신일 RS 랭킹
    _, rows = store.pivot_latest(
        con, "us_sector",
        {"ret21": "ret_21", "rs21": "rs_21", "rs63": "rs_63",
         "quad": "quadrant", "rsi": "rsi", "hot": "overheat"},
        date=store.latest_date(con, "us_sector", "rs_21"),
        order_by="rs21 DESC",
    )
    names = cfg.get("names", {})
    print(f"{'sym':6} {'name':8} {'ret21%':>7} {'rs21':>6} {'rs63':>6} {'quad':>10} {'rsi':>5} hot")
    for r in rows:
        quad = QUAD_NAMES.get(int(r["quad"]), "-") if r["quad"] is not None else "-"
        print(
            f"{r['code']:6} {names.get(r['code'], ''):8} "
            f"{r['ret21'] if r['ret21'] is not None else '-':>7} "
            f"{r['rs21'] if r['rs21'] is not None else '-':>6} "
            f"{r['rs63'] if r['rs63'] is not None else '-':>6} "
            f"{quad:>10} "
            f"{r['rsi'] if r['rsi'] is not None else '-':>5} "
            f"{'*' if r['hot'] else ''}"
        )
    con.close()


def run_kr():
    cfg = config.load()["kr"]
    sectors = cfg["sector_codes"]
    bench = cfg["benchmark"]

    con = db.connect()
    relative_strength.compute(con, "kr_sector", sectors, bench)
    rotation.compute(con, "kr_sector", sectors, bench)
    overheat.compute(con, "kr_sector", sectors + [bench])
    leaders.compute_sector(con, "kr_sector")
    participation.compute(con, "kr_sector", "KR",
                          anchor_date=store.latest_date(con, "kr_sector", "rs_21"))
    n = leaders.compute_stocks(con, scope="kr_stock", market="KR", bench=bench)
    print(f"kr_stock leader rows: {n}")

    names = {
        r["stock_code"]: r["name"]
        for r in con.execute("SELECT stock_code, name FROM sector_map WHERE market='KR_INDEX'")
    }
    _, rows = store.pivot_latest(
        con, "kr_sector",
        {"rs21": "rs_21", "rs63": "rs_63", "quad": "quadrant", "score": "leader_score"},
        date=store.latest_date(con, "kr_sector", "rs_21"),
        order_by="(score IS NULL), score DESC",
    )
    print(f"{'code':6} {'name':12} {'score':>6} {'rs21':>6} {'rs63':>6} {'quad':>10}")
    for r in rows[:12]:
        quad = QUAD_NAMES.get(int(r["quad"]), "-") if r["quad"] is not None else "-"
        print(f"{r['code']:6} {names.get(r['code'], ''):12} "
              f"{r['score'] if r['score'] is not None else '-':>6} "
              f"{r['rs21'] if r['rs21'] is not None else '-':>6} "
              f"{r['rs63'] if r['rs63'] is not None else '-':>6} {quad:>10}")
    con.close()


def main():
    p = argparse.ArgumentParser(description="market-hub 분석")
    p.add_argument("--us", action="store_true", help="미국 섹터 분석")
    p.add_argument("--kr", action="store_true", help="한국 섹터 분석 (KRX 데이터 필요)")
    args = p.parse_args()
    if args.us:
        run_us()
    if args.kr:
        run_kr()
    if not (args.us or args.kr):
        p.print_help()


if __name__ == "__main__":
    main()
