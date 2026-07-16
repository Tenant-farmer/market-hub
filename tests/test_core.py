"""순수 로직 + 저장 계층 스모크 테스트."""
import sqlite3

import pandas as pd

from src.analytics import store
from src.analytics.data import _trim_ragged_tail
from src.analytics.leaders import _pct_rank
from src.analytics.rotation import _quadrant, current_streak
from src.collectors.gurus import _normalize_units, _quarter
from src.dashboard.fmt import fmt_usd


def _mem_con():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute(
        "CREATE TABLE analytics_daily (date TEXT, scope TEXT, code TEXT, metric TEXT, value REAL, "
        "PRIMARY KEY (date, scope, code, metric))"
    )
    return con


def test_store_replace_and_pivot():
    con = _mem_con()
    # 스테일 날짜(과거 실행 잔재)가 남아있는 상황
    store.replace_metrics(con, "s", ["m1"], [("2026-01-02", "s", "AAA", "m1", 1.0)])
    n = store.replace_metrics(con, "s", ["m1", "m2"], [
        ("2026-01-01", "s", "AAA", "m1", 10.0),
        ("2026-01-01", "s", "AAA", "m2", 20.0),
        ("2026-01-01", "s", "BBB", "m1", 30.0),
    ])
    assert n == 3
    date, rows = store.pivot_latest(con, "s", {"a": "m1", "b": "m2"}, order_by="a DESC")
    assert date == "2026-01-01"          # 스테일 01-02가 삭제되어 최신일이 정확
    assert rows[0]["code"] == "BBB" and rows[0]["a"] == 30.0 and rows[0]["b"] is None
    assert rows[1]["b"] == 20.0


def test_quadrant():
    assert _quadrant(101, 101) == 1  # Leading
    assert _quadrant(101, 99) == 2   # Weakening
    assert _quadrant(99, 99) == 3    # Lagging
    assert _quadrant(99, 101) == 4   # Improving


def test_current_streak():
    assert current_streak([True, False, True, True]) == 2
    assert current_streak([True, True, False]) == 0
    assert current_streak([]) == 0


def test_pct_rank():
    r = _pct_rank({"a": 1.0, "b": 3.0, "c": 2.0})
    assert r["a"] == 0.0 and r["b"] == 1.0 and r["c"] == 0.5
    assert _pct_rank({"only": 5.0}) == {"only": 1.0}


def test_trim_ragged_tail():
    px = pd.DataFrame(
        {"A": [1, 2, 3], "B": [1, 2, None], "C": [1, 2, None]},
        index=["d1", "d2", "d3"],
    )
    trimmed = _trim_ragged_tail(px, min_frac=0.8)
    assert list(trimmed.index) == ["d1", "d2"]  # 커버리지 1/3인 d3 제거


def test_fmt_usd():
    assert fmt_usd(5.13e12) == "$5.13T"
    assert fmt_usd(2.9e9) == "$2.9B"
    assert fmt_usd(-6.13e8) == "$-613M"
    assert fmt_usd(5000) == "$5K"


def test_guru_quarter():
    assert _quarter("2026-03-31") == "2026Q1"
    assert _quarter("2025-12-31") == "2025Q4"


def test_vix_signal_states():
    from src.dashboard.queries import classify_vix_signal as c
    assert c(16, 105, False)["state"] == "hold_pre"    # 평온 속 헤지 수요 = 전조
    assert c(25, 90, False)["state"] == "hold_trap"    # 공포 없는 하락 초입
    assert c(25, 100, False)["state"] == "buy1"        # 급성 공포
    assert c(32, 100, False)["state"] == "buy2"        # 분할 매수
    assert c(40, 130, True)["state"] == "buy3"         # 공포 정점 통과
    assert c(40, 130, False)["state"] == "buy2"        # 아직 냉각 전이면 분할까지만
    assert c(15, 85, False)["state"] == "neutral"
    # F&G 회피 축: 평온장 극단탐욕만 잡고, 다른 상태엔 간섭 없음
    assert c(15, 85, False, fng=80)["state"] == "avoid_greed"
    assert c(15, 85, False, fng=47)["state"] == "neutral"
    assert c(16, 105, False, fng=80)["state"] == "hold_pre"   # 전조가 우선
    assert c(32, 100, False, fng=90)["state"] == "buy2"       # 매수 구간엔 무간섭


def test_guru_normalize_units():
    # 천달러 단위 제출 (총액 $3.4M로 보임) → 천 배 보정
    h, tot = _normalize_units([("C1", "N1", 3_000_000.0, 10), ("C2", "N2", 400_000.0, 5)])
    assert tot == 3_400_000_000.0
    assert h[0][2] == 3_000_000_000.0
    # 달러 단위 제출은 그대로
    h2, tot2 = _normalize_units([("C1", "N1", 5e10, 10)])
    assert tot2 == 5e10 and h2[0][2] == 5e10
