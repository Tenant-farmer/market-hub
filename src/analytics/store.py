"""analytics_daily 공용 저장/조회.

모든 분석 모듈의 공통 패턴을 한곳에:
  - replace_metrics: 전체 재계산 → 자기 metric 삭제 → 일괄 저장 (스테일 행 방지)
  - pivot_latest:    최신일 기준 metric 피벗 조회 (대시보드/CLI 공용)

주의: metric/alias 이름은 코드 상수에서만 오므로 f-string SQL 안전.
"""
from src import db

COLUMNS = ["date", "scope", "code", "metric", "value"]


def replace_metrics(con, scope: str, metrics: list[str], rows: list[tuple]) -> int:
    ph = ",".join("?" * len(metrics))
    con.execute(
        f"DELETE FROM analytics_daily WHERE scope=? AND metric IN ({ph})",
        [scope, *metrics],
    )
    return db.upsert(con, "analytics_daily", COLUMNS, rows)


def latest_date(con, scope: str, metric: str) -> str | None:
    return con.execute(
        "SELECT MAX(date) d FROM analytics_daily WHERE scope=? AND metric=?",
        (scope, metric),
    ).fetchone()["d"]


def pivot_latest(con, scope: str, aliases: dict[str, str],
                 date: str | None = None, order_by: str | None = None):
    """aliases: {컬럼별칭: metric}. 반환: (기준일, rows[dict])."""
    if date is None:
        date = latest_date(con, scope, next(iter(aliases.values())))
    if date is None:
        return None, []
    sel = ", ".join(f"MAX(CASE WHEN metric='{m}' THEN value END) {a}" for a, m in aliases.items())
    q = f"SELECT code, {sel} FROM analytics_daily WHERE scope=? AND date=? GROUP BY code"
    if order_by:
        q += f" ORDER BY {order_by}"
    return date, [dict(r) for r in con.execute(q, (scope, date))]
