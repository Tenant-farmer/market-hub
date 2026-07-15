"""수집 CLI.

python collect.py --us                  # 미국 섹터 최근 7일
python collect.py --us --backfill 730   # 2년 백필
python collect.py --sentiment           # F&G / 풋콜 / VIX
python collect.py --all
"""
import argparse

from dotenv import load_dotenv

from src.collectors import base, gurus, kr_flows, kr_sectors, sentiment, us_sectors, us_stocks


def main():
    load_dotenv()
    p = argparse.ArgumentParser(description="market-hub 데이터 수집")
    p.add_argument("--us", action="store_true", help="미국 섹터 ETF EOD")
    p.add_argument("--us-stocks", action="store_true", help="미국 개별종목 (S&P500)")
    p.add_argument("--kr", action="store_true", help="KR 업종지수 (KRX 계정 필요)")
    p.add_argument("--kr-flows", action="store_true", help="KR 투자자별 수급 (KRX 계정 필요)")
    p.add_argument("--kr-map", action="store_true", help="KR 업종 구성종목 갱신 (주 1회)")
    p.add_argument("--gurus", action="store_true", help="구루 13F (SEC EDGAR)")
    p.add_argument("--sentiment", action="store_true", help="심리지표 (F&G, 풋콜, VIX)")
    p.add_argument("--all", action="store_true", help="전체 수집 (KR은 계정 있을 때만)")
    p.add_argument("--backfill", type=int, default=0, metavar="DAYS", help="과거 N일 백필")
    args = p.parse_args()

    days = args.backfill or 7
    ran = False
    if args.us or args.all:
        base.run_collector("us_sectors", lambda con: us_sectors.collect(con, days=days))
        ran = True
    if args.us_stocks or args.all:
        base.run_collector("us_stocks", lambda con: us_stocks.collect(con, days=days))
        ran = True
    if args.kr or args.all:
        base.run_collector("kr_sectors", lambda con: kr_sectors.collect(con, days=days))
        ran = True
    if args.kr_map:
        base.run_collector("kr_map", kr_sectors.refresh_constituents)
        ran = True
    if args.kr_flows or args.all:
        base.run_collector("kr_flows", lambda con: kr_flows.collect(con, days=days))
        ran = True
    if args.gurus or args.all:
        base.run_collector("gurus", gurus.collect)
        ran = True
    if args.sentiment or args.all:
        base.run_collector("sentiment", sentiment.collect)
        ran = True
    if not ran:
        p.print_help()


if __name__ == "__main__":
    main()
