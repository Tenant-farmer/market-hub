"""백테스트 캐시 공용 로더 — 짧은 캐시로 조용히 도는 사고 방지 가드.

2026-07-27 사고: virtual.refresh_prices(라이브 400일)가 11년 백테스트 캐시를 덮어써
검증이 1.5년 구간으로 돌았다(무의미). 재발 방지로 **최소 연수를 강제**한다.
운영 캐시(us_px_live.pkl)와 검증 캐시(us_px_cache.pkl)는 파일이 분리돼 있다.

사용: from _bt_cache import load_cache;  px, spy = load_cache()
"""
import pickle
import sys
from pathlib import Path

CACHE = Path(__file__).resolve().parents[1] / "data" / "us_px_cache.pkl"
MIN_YEARS = 5.0


def load_cache(min_years: float = MIN_YEARS, min_symbols: int = 200):
    """검증용 장기 캐시 로드. 기간·종목수 미달이면 즉시 중단(조용한 오검증 방지)."""
    if not CACHE.exists():
        sys.exit(f"[중단] 백테스트 캐시 없음: {CACHE}\n"
                 f"  복구: scripts/rebuild_bt_cache.py 실행")
    px, spy = pickle.loads(CACHE.read_bytes())
    yrs = (px.index[-1] - px.index[0]).days / 365.25
    if yrs < min_years or px.shape[1] < min_symbols:
        sys.exit(f"[중단] 캐시가 검증에 부적합: {px.shape[1]}종목 · {yrs:.1f}년 "
                 f"(요구: {min_symbols}종목 · {min_years}년)\n"
                 f"  기간 {px.index[0].date()}~{px.index[-1].date()}\n"
                 f"  → 라이브 캐시로 덮어썼을 가능성. scripts/rebuild_bt_cache.py 로 복구")
    return px, spy
