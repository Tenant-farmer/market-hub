"""매시간 실행되는 수집 라우터 — 시장 상태에 맞는 것만 수집한다.

Windows 작업 스케줄러(매시간)가 run_hourly.bat → 이 모듈을 호출한다.
시간대는 로컬(KST) 기준. 결과는 collector_runs(→ /health)와 data/scheduler.log에 남는다.

라우팅:
- 매번: 심리지표 (가볍고 24시간 갱신)
- KR 장중 (평일 09:00~16:15): 업종지수·종목 스냅샷·수급 → KR 분석 (준실시간)
- US 장중 (KST 평일밤 22:30~ / 화~토 새벽 ~05:15): 섹터 ETF → US 분석
- 아침 슬롯 (06~09시, 하루 1회): US 전종목+시총, 구루 13F, US 분석 (전일 마감 확정치)
- 월요일 아침 (주 1회): KR 업종 구성종목 갱신
"""
from datetime import date, datetime

from dotenv import load_dotenv

load_dotenv()

from src import db
from src.collectors import (
    base, dart, earnings, econ_calendar, ecos, fed, gurus, kr_capex, kr_flows,
    kr_sectors, kr_stocks, macro, news, sentiment, us_capex, us_sectors, us_stocks,
    vkospi,
)


def _ran_today(con, collector: str) -> bool:
    return con.execute(
        "SELECT 1 FROM collector_runs WHERE collector=? AND status='ok' AND run_at >= ? LIMIT 1",
        (collector, date.today().isoformat()),
    ).fetchone() is not None


def main():
    now = datetime.now()
    wd, hm = now.weekday(), now.hour * 100 + now.minute
    print(f"--- hourly {now.isoformat(timespec='seconds')} ---")

    kr_session = wd < 5 and 900 <= hm <= 1615
    # US 정규장: KST 22:30~다음날 05:00 (서머타임 아닐 땐 23:30~06:00 — 여유 있게 잡음)
    us_session = (wd < 5 and hm >= 2230) or (wd in (1, 2, 3, 4, 5) and hm <= 615)
    morning = 600 <= hm <= 859

    base.run_collector("sentiment", sentiment.collect)
    base.run_collector("macro", lambda c: macro.collect(c, days=5))
    base.run_collector("news", news.collect)
    base.run_collector("dart", dart.collect)      # 공시 (DART_API_KEY 없으면 0건 통과)

    con = db.connect()
    from src.jobs import watchdog

    watchdog.check_engine(con)     # 엔진 워커 생존 감시 (정체 시 텔레그램 경보)
    try:                           # 계좌 에쿼티 스냅샷 (하루 마지막 값이 EOD 근사)
        from src.trading import portfolio

        portfolio.snapshot(con)
        con.commit()
    except Exception as e:
        print("  [portfolio] 스냅샷 실패:", str(e)[:80])
    try:                           # 일일 매매일지 갱신 (docs/journal/YYYY-MM-DD.md)
        from src.jobs import journal

        journal.write_today(con)
    except Exception as e:
        print("  [journal] 일지 실패:", str(e)[:80])
    ran_kr = ran_us = False

    if kr_session:
        base.run_collector("kr_sectors", lambda c: kr_sectors.collect(c, days=5))
        base.run_collector("kr_stocks", lambda c: kr_stocks.collect(c, days=3))
        base.run_collector("kr_flows", lambda c: kr_flows.collect(c, days=90))
        ran_kr = True

    if us_session:
        base.run_collector("us_sectors", lambda c: us_sectors.collect(c, days=5))
        ran_us = True

    if morning:
        # 로컬 백업 하루 1회 (DB 스냅샷 + 설정 zip, 최근 14개 유지)
        if not _ran_today(con, "backup"):
            from src.jobs import backup

            base.run_collector("backup", backup.run)
        # 전일 마감 확정치 하루 1회 (실패 시 다음 시간에 재시도됨)
        if wd < 6 and not _ran_today(con, "us_stocks"):
            base.run_collector("us_sectors", lambda c: us_sectors.collect(c, days=7))
            base.run_collector("us_stocks", lambda c: us_stocks.collect(c, days=7))
            base.run_collector("gurus", gurus.collect)
            base.run_collector("earnings", earnings.collect)
            base.run_collector("econ_calendar", econ_calendar.collect)
            base.run_collector("fed", fed.collect)
            ran_us = True
        if wd < 5 and not _ran_today(con, "kr_sectors"):
            base.run_collector("kr_sectors", lambda c: kr_sectors.collect(c, days=5))
            base.run_collector("kr_flows", lambda c: kr_flows.collect(c, days=90))
            ran_kr = True
        # 한은 거시 (기준금리·국고금리·CPI) 하루 1회 — 키 없으면 0건 통과
        if wd < 6 and not _ran_today(con, "ecos"):
            base.run_collector("ecos", ecos.collect)
        # VKOSPI (KRX Open API) 하루 1회 — 키 없으면 0건 통과
        if wd < 6 and not _ran_today(con, "vkospi"):
            base.run_collector("vkospi", vkospi.collect)
        if wd == 0 and not _ran_today(con, "kr_map"):
            base.run_collector("kr_map", kr_sectors.refresh_constituents)
        # CapEx는 분기 공시 — 25일 이상 지났으면 재수집 (사실상 월 1회)
        for cname, mod in (("us_capex", us_capex), ("kr_capex", kr_capex)):
            fresh_row = con.execute(
                "SELECT 1 FROM collector_runs WHERE collector=? AND status='ok' "
                "AND run_at >= datetime('now', 'localtime', '-25 days') LIMIT 1",
                (cname,),
            ).fetchone()
            if wd < 6 and not fresh_row:
                base.run_collector(cname, mod.collect)
    con.close()

    import analyze  # 루트 모듈 (bat이 CWD를 리포 루트로 보장)

    if ran_us:
        analyze.run_us()
    if ran_kr:
        analyze.run_kr()
    if not (ran_us or ran_kr):
        print("장외 시간 - 심리지표만 갱신")

    # 아침 브리핑 (하루 1회, 토큰 설정 시에만)
    import os

    if morning and os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"):
        con2 = db.connect()
        if not _ran_today(con2, "telegram_brief"):
            from src.jobs import briefing

            base.run_collector("telegram_brief", briefing.send_briefing)
        con2.close()


if __name__ == "__main__":
    main()
