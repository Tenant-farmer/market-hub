"""분석용 데이터 로더."""
import pandas as pd


def load_field(con, symbols: list[str], field: str = "close") -> pd.DataFrame:
    """prices_daily에서 지정 컬럼을 (date × symbol) 피벗으로 로드.

    소스(야후)의 EOD 확정 지연으로 최신 1~2일은 일부 심볼만 존재할 수 있다.
    그런 불완전한 꼬리 날짜는 잘라낸다 — 안 그러면 최신일 분석이
    데이터가 있는 소수 심볼로만 계산된다.
    """
    assert field in ("open", "high", "low", "close", "volume", "value")
    ph = ",".join("?" * len(symbols))
    df = pd.read_sql_query(
        f"SELECT date, symbol, {field} FROM prices_daily WHERE symbol IN ({ph}) ORDER BY date",
        con,
        params=symbols,
    )
    px = df.pivot(index="date", columns="symbol", values=field)
    return _trim_ragged_tail(px)


def load_closes(con, symbols: list[str]) -> pd.DataFrame:
    return load_field(con, symbols, "close")


def _trim_ragged_tail(px: pd.DataFrame, min_frac: float = 0.8) -> pd.DataFrame:
    """심볼 커버리지가 min_frac 미만인 꼬리 날짜 제거."""
    frac = px.notna().mean(axis=1)
    last_full = frac[frac >= min_frac].index
    if len(last_full) == 0:
        return px
    return px.loc[:last_full[-1]]
