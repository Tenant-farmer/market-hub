"""캔들 패턴 인식 — 15종, pandas 벡터화 (외부 의존 없음).

용도: **참고 지식**. 매매 규칙엔 넣지 않는다 —
6전략 백테스트(2026-07-27)에서 패턴 계열(브레이크아웃·볼린저 스퀴즈)이 SPY에 졌고,
캔들 패턴은 그보다도 표본 노이즈가 크다. 종목 상세에서 '지금 이런 모양이다'를 보여주는 용도.

각 패턴은 (조건, 방향, 설명)으로 정의. 방향: bull(반등 시사) / bear(하락 시사) / neutral.
호출: detect(df) — df는 open/high/low/close 컬럼을 가진 DataFrame (최근 N일)
"""
import numpy as np
import pandas as pd


def _body(df):
    return (df["close"] - df["open"]).abs()


def _range(df):
    return (df["high"] - df["low"]).replace(0, np.nan)


def _upper(df):
    return df["high"] - df[["open", "close"]].max(axis=1)


def _lower(df):
    return df[["open", "close"]].min(axis=1) - df["low"]


def _bull(df):
    return df["close"] > df["open"]


def detect(df: pd.DataFrame) -> pd.DataFrame:
    """일별 패턴 플래그 DataFrame 반환 (컬럼=패턴명, 값=bool)."""
    if df is None or len(df) < 5:
        return pd.DataFrame()
    d = df.copy()
    b, rng, up, lo = _body(d), _range(d), _upper(d), _lower(d)
    bull, prev = _bull(d), d.shift(1)
    pb, pbull = _body(prev), _bull(prev)
    small = b <= rng * 0.3
    out = pd.DataFrame(index=d.index)

    # ── 단일 캔들 (5종) ────────────────────────────────
    out["망치형"] = small & (lo >= b * 2) & (up <= b * 0.5)              # 하단 긴 꼬리 = 반등
    out["역망치형"] = small & (up >= b * 2) & (lo <= b * 0.5)
    out["교수형"] = out["망치형"] & (d["close"] < d["close"].rolling(10).mean())
    out["도지"] = b <= rng * 0.1                                        # 매수-매도 균형
    out["마루보즈"] = (b >= rng * 0.95)                                  # 꼬리 없는 강한 방향성

    # ── 2캔들 (6종) ───────────────────────────────────
    out["상승장악형"] = (~pbull & bull & (d["close"] >= prev["open"])      # 전일 음봉 통째로 삼킴
                        & (d["open"] <= prev["close"]))
    out["하락장악형"] = (pbull & ~bull & (d["close"] <= prev["open"])
                        & (d["open"] >= prev["close"]))
    out["관통형"] = (~pbull & bull & (d["open"] < prev["low"])
                    & (d["close"] > prev[["open", "close"]].mean(axis=1)))
    out["흑운형"] = (pbull & ~bull & (d["open"] > prev["high"])
                    & (d["close"] < prev[["open", "close"]].mean(axis=1)))
    out["잉태형"] = (b < pb * 0.6) & (d[["open", "close"]].max(axis=1) < prev[["open", "close"]].max(axis=1)) \
        & (d[["open", "close"]].min(axis=1) > prev[["open", "close"]].min(axis=1))
    out["집게바닥"] = (~pbull & bull & ((d["low"] - prev["low"]).abs() <= rng * 0.1))

    # ── 3캔들 (4종) ───────────────────────────────────
    p2 = d.shift(2)
    p2bull = _bull(p2)
    out["샛별형"] = (~p2bull & (_body(prev) <= _range(prev) * 0.3) & bull      # 바닥 반전
                    & (d["close"] > p2[["open", "close"]].mean(axis=1)))
    out["석별형"] = (p2bull & (_body(prev) <= _range(prev) * 0.3) & ~bull
                    & (d["close"] < p2[["open", "close"]].mean(axis=1)))
    out["적삼병"] = bull & pbull & p2bull & (d["close"] > prev["close"]) \
        & (prev["close"] > p2["close"])                                       # 3연속 상승
    out["흑삼병"] = ~bull & ~pbull & ~p2bull & (d["close"] < prev["close"]) \
        & (prev["close"] < p2["close"])
    return out.fillna(False)


DIRECTION = {
    "망치형": "bull", "역망치형": "bull", "교수형": "bear", "도지": "neutral",
    "마루보즈": "neutral", "상승장악형": "bull", "하락장악형": "bear",
    "관통형": "bull", "흑운형": "bear", "잉태형": "neutral", "집게바닥": "bull",
    "샛별형": "bull", "석별형": "bear", "적삼병": "bull", "흑삼병": "bear",
}
# 실측 성적 (scripts/candle_backtest.py, 503종목 10년, 177,533건, 감지 다음날 진입 SPY초과)
# → **15개 패턴 전부 +21일 초과수익 0.5%p 미만 = 예측력 없음**. UI에 함께 표시해 맹신 방지.
STATS = {
    "관통형": (4150, 0.31, 50), "교수형": (2492, 0.21, 48), "도지": (26366, 0.05, 47),
    "마루보즈": (2507, 0.09, 48), "망치형": (5432, 0.21, 48), "상승장악형": (9638, 0.21, 49),
    "샛별형": (12969, -0.15, 46), "석별형": (12276, -0.10, 47), "역망치형": (5014, 0.11, 48),
    "잉태형": (27835, 0.03, 47), "적삼병": (22828, -0.01, 47), "집게바닥": (11647, 0.10, 48),
    "하락장악형": (10192, 0.04, 47), "흑삼병": (19494, -0.08, 47), "흑운형": (4693, 0.64, 49),
}

DESC = {
    "망치형": "하단 긴 꼬리 — 저가 매수세 유입",
    "역망치형": "상단 긴 꼬리 — 반등 시도",
    "교수형": "고점권 망치 — 상승 피로",
    "도지": "매수·매도 균형 — 방향 전환 가능",
    "마루보즈": "꼬리 없는 장대 — 강한 일방향",
    "상승장악형": "전일 음봉을 통째로 삼킨 양봉",
    "하락장악형": "전일 양봉을 통째로 삼킨 음봉",
    "관통형": "갭하락 후 전일 몸통 절반 이상 회복",
    "흑운형": "갭상승 후 전일 몸통 절반 이하 하락",
    "잉태형": "전일 몸통 안에 갇힌 작은 캔들 — 관망",
    "집게바닥": "이틀 연속 같은 저가 — 지지 확인",
    "샛별형": "하락→소캔들→양봉 3일 바닥 반전",
    "석별형": "상승→소캔들→음봉 3일 천장 반전",
    "적삼병": "3연속 양봉 — 추세 강화",
    "흑삼병": "3연속 음봉 — 추세 약화",
}


def latest(df: pd.DataFrame, days: int = 3) -> list[dict]:
    """최근 days일 내 감지된 패턴 목록 (최신순)."""
    flags = detect(df)
    if flags.empty:
        return []
    out = []
    for i in range(len(flags) - 1, max(-1, len(flags) - 1 - days), -1):
        row = flags.iloc[i]
        for name in flags.columns:
            if bool(row[name]):
                n, a21, win = STATS.get(name, (0, 0.0, 0))
                out.append({
                    "date": str(flags.index[i])[:10], "pattern": name,
                    "dir": DIRECTION.get(name, "neutral"), "desc": DESC.get(name, ""),
                    "n": n, "edge21": a21, "win21": win,      # 실측 성적 (맹신 방지)
                    "useful": abs(a21) >= 0.5,                # 전부 False — 예측력 없음
                })
    return out


if __name__ == "__main__":
    import sys

    from dotenv import load_dotenv

    load_dotenv()
    sys.path.insert(0, ".")
    from src import db

    c = db.connect()
    sym = sys.argv[1] if len(sys.argv) > 1 else "005930"
    rows = c.execute("SELECT date, open, high, low, close FROM prices_daily WHERE symbol=? "
                     "ORDER BY date DESC LIMIT 60", (sym,)).fetchall()
    c.close()
    if not rows:
        sys.exit(f"{sym} 데이터 없음")
    df = pd.DataFrame([dict(r) for r in rows][::-1]).set_index("date")
    print(f"=== {sym} 최근 5일 캔들 패턴 ===")
    for p in latest(df, days=5):
        icon = {"bull": "🟢", "bear": "🔴", "neutral": "⚪"}[p["dir"]]
        print(f"  {p['date']} {icon} {p['pattern']} — {p['desc']}")
    if not latest(df, days=5):
        print("  감지된 패턴 없음")
