"""VPS 이전 판정용 스모크 테스트 — 데이터센터 IP에서 KRX·야후가 되는지 실측.

목적: VPS를 빌린 직후 여기서 실행해, 전체 이전 전에 데이터 소스가 살아있는지 판정한다.
가장 중요한 건 pykrx 수급·공매도(공식 OpenAPI가 커버 못 하는 우리 차별화)와 yfinance 429 여부.

사용법 (VPS, Linux):
  python3 -m venv .venv && . .venv/bin/activate
  pip install pykrx yfinance python-dotenv requests
  export KRX_ID=... KRX_PW=...          # 또는 이 폴더에 .env
  python scripts/vps_smoketest.py

종료 코드: 0 = 이전 가능(핵심 통과), 1 = 재검토 필요(핵심 실패).
"""
import os
import sys
import time
from datetime import date, timedelta

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    pass

CODE = "005930"          # 삼성전자
END = date.today().strftime("%Y%m%d")
START = (date.today() - timedelta(days=15)).strftime("%Y%m%d")
results = []             # (이름, 핵심여부, 통과, 상세)


def check(name, critical, fn):
    t0 = time.time()
    try:
        detail = fn()
        ok = True
    except Exception as e:
        detail = f"{type(e).__name__}: {str(e)[:120]}"
        ok = False
    dt = time.time() - t0
    results.append((name, critical, ok, f"{detail}  ({dt:.1f}s)"))
    mark = "PASS" if ok else "FAIL"
    star = " *핵심*" if critical else ""
    print(f"[{mark}] {name}{star}: {detail}  ({dt:.1f}s)")


def ip_info():
    import requests

    r = requests.get("https://ipinfo.io/json", timeout=10)
    d = r.json()
    return f"{d.get('ip')} / {d.get('country')} / {d.get('org','')[:40]}"


def krx_prices():
    from pykrx import stock

    df = stock.get_market_ohlcv(START, END, CODE)
    assert len(df) > 0, "행 없음"
    return f"{len(df)}행, 최근종가 {int(df['종가'].iloc[-1]):,}"


def krx_flows():
    from pykrx import stock

    df = stock.get_market_trading_value_by_date(START, END, CODE)
    assert len(df) > 0, "행 없음"
    return f"{len(df)}행, 투자자열 {list(df.columns)[:3]}"


def krx_short():
    from pykrx import stock

    df = stock.get_shorting_balance_by_date(START, END, CODE)
    assert len(df) > 0, "행 없음"
    return f"{len(df)}행"


def krx_snapshot():
    from pykrx import stock

    df = stock.get_market_ohlcv_by_ticker(END, market="KOSPI")
    assert len(df) > 100, "종목 수 비정상"
    return f"KOSPI 전체 {len(df)}종목"


def yf_us():
    import yfinance as yf

    for _ in range(2):
        df = yf.Ticker("AAPL").history(period="5d", interval="1d")
        if len(df):
            return f"AAPL {len(df)}행, 종가 {df['Close'].iloc[-1]:.2f}"
        time.sleep(1)
    raise RuntimeError("빈 응답 (rate limit 의심)")


def yf_kr():
    import yfinance as yf

    df = yf.Ticker(f"{CODE}.KS").history(period="5d", interval="1d")
    assert len(df) > 0, "빈 응답 (rate limit 의심)"
    return f"{CODE}.KS {len(df)}행"


print("=" * 64)
print("market-hub VPS 스모크 테스트")
print("=" * 64)
if not (os.getenv("KRX_ID") and os.getenv("KRX_PW")):
    print("경고: KRX_ID/KRX_PW 미설정 — KRX 항목이 전부 실패합니다.\n")

check("서버 IP/국가", False, ip_info)
print("-" * 64)
check("KRX 일별시세(OHLCV)", False, krx_prices)      # 공식 OpenAPI에도 있음
check("KRX 투자자 수급", True, krx_flows)             # 공식 OpenAPI 미커버 → 핵심
check("KRX 공매도 잔고", True, krx_short)             # 공식 OpenAPI 미커버 → 핵심
check("KRX 전체 스냅샷", False, krx_snapshot)         # 무거운 호출 (kr_stocks)
time.sleep(1)
print("-" * 64)
check("yfinance US(AAPL)", True, yf_us)              # 429 나면 여기서
check("yfinance KR(.KS)", False, yf_kr)

print("=" * 64)
crit = [r for r in results if r[1]]
crit_fail = [r for r in crit if not r[2]]
any_fail = [r for r in results if not r[2]]
if not crit_fail:
    verdict = "이전 가능 — 핵심 소스 전부 통과" + (
        f" (비핵심 {len(any_fail)}건 실패: 완화책 검토)" if any_fail else ""
    )
    code = 0
else:
    verdict = "재검토 필요 — 핵심 실패: " + ", ".join(r[0] for r in crit_fail)
    code = 1
print("판정:", verdict)
print("=" * 64)
sys.exit(code)
