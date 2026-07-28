"""매수 신호등 — **실주문 판단의 근거**. 화면·알림·매매가 모두 이 한 곳을 본다.

왜 여기로 왔나 (2026-07-28): 원래 `src/dashboard/queries_macro.py`에 있었다. 그 파일의
자기소개는 "개요 페이지의 시장 컨텍스트/신호등 영역"인데, 실제 소비자는 6곳이었고
그중 하나가 **실주문을 내는 `trading/signal_entry.py`**였다. 표시용으로 고친 코드가
매매 판단을 조용히 바꿀 수 있는 구조였고, 부작용으로 **엔진 프로세스가 Flask를
통째로 메모리에 올리고** 있었다(실측 확인).

신호는 표현이 아니라 도메인이다 → analytics로 내리고, 대시보드가 거꾸로 임포트한다.
`queries_macro`가 re-export하므로 화면 코드는 한 줄도 바뀌지 않는다.
"""
from datetime import date


def series(con, sym: str, n: int = 270) -> list[float]:
    """최근 n개 종가 (오래된 것 → 최신 순)."""
    rows = con.execute(
        "SELECT close FROM prices_daily WHERE symbol=? ORDER BY date DESC LIMIT ?", (sym, n)
    ).fetchall()
    return [r["close"] for r in reversed(rows)]


def stale_days(con, sym: str) -> int | None:
    """마지막 수집일이 며칠 전인가 — 묵은 값으로 신호가 도는 걸 드러내기 위함.

    2026-07-27 실측: KRX가 당일 지수를 아침 수집 시각(06:07) 이후에 게시해 VKOSPI가
    1거래일 밀린 채 KR 매수신호가 계속 'buy'를 냈다. 값 자체는 맞지만 **최신이 아니었다**.
    """
    try:
        r = con.execute("SELECT MAX(date) d FROM prices_daily WHERE symbol=? AND close IS NOT NULL",
                        (sym,)).fetchone()
        if not r or not r["d"]:
            return None
        return (date.today() - date.fromisoformat(r["d"])).days
    except Exception:
        return None


def classify_vix_signal(vix: float, vvix: float, cooling: bool, fng: float | None = None) -> dict:
    """매수 신호등 — 근거: scripts/vvix_backtest.py + fng_backtest.py.

    보류: [VIX<20 & VVIX≥95](전조) / [VIX 20~30 & VVIX<95](함정)
    매수: [VIX 20~30 & VVIX≥95](승률 84%) / VIX 30+ / VIX 35+ & VVIX 냉각(적극)
    회피: 평온장(VIX<20 & VVIX<95)인데 F&G≥75 (극단탐욕: 승률 79→57%)
    """
    if vix >= 35 and cooling:
        return {"state": "buy3", "emoji": "🟢🟢", "cls": "pos",
                "label": "적극 매수 — 공포 정점 통과",
                "desc": "VIX 35+ & VVIX 냉각 · 3개월 중앙값 +9.8%"}
    if vix >= 30:
        return {"state": "buy2", "emoji": "🟢", "cls": "pos",
                "label": "분할 매수 구간",
                "desc": "VIX 30+ · 역사적 승률 72~83%"}
    if vix >= 20 and vvix >= 95:
        return {"state": "buy1", "emoji": "🟢", "cls": "pos",
                "label": "1차 매수 구간 — 급성 공포",
                "desc": "VIX 20~30 & VVIX 95+ · 승률 84% · 중앙값 +6.9%"}
    if vix >= 20:
        return {"state": "hold_trap", "emoji": "🔴", "cls": "neg",
                "label": "매수 보류 — 함정 구간",
                "desc": "공포 없는 하락 초입 (VIX 20~30 & VVIX<95) · 승률 65%"}
    if vvix >= 95:
        return {"state": "hold_pre", "emoji": "🔴", "cls": "neg",
                "label": "매수 보류 — 전조 경보",
                "desc": "평온 속 크래시 헤지 수요 급증 · 승률 66%"}
    if fng is not None and fng >= 75:
        return {"state": "avoid_greed", "emoji": "🟠", "cls": "hot",
                "label": "과열 주의 — 신규매수 자제",
                "desc": "평온장 극단탐욕 (F&G 75+) · 승률 79→57%, 중앙값 +4.7→+1.6%"}
    return {"state": "neutral", "emoji": "⚪", "cls": "",
            "label": "평시 — 신호 없음",
            "desc": "레짐·주도주 신호를 따르세요"}


def vix_signal(con):
    vix = series(con, "^VIX", 5)
    vvix = series(con, "^VVIX", 5)
    if not vix or len(vvix) < 5:
        return None
    v, w = vix[-1], vvix[-1]
    w5 = sum(vvix[-5:]) / 5
    fng_row = con.execute(
        "SELECT value FROM sentiment_daily WHERE metric='fear_greed' ORDER BY date DESC LIMIT 1"
    ).fetchone()
    fng = fng_row["value"] if fng_row else None
    sig = classify_vix_signal(v, w, cooling=w < w5, fng=fng)
    sig.update({"vix": v, "vvix": w, "vvix5": w5, "fng": fng})
    return sig


def kr_signal(con):
    """KR 전용 매수신호 — VKOSPI≥30 & KOSPI 52주 고점 대비 -5% 이하.

    근거: scripts/vkospi_backtest.py (2010~24: +63d 승률 75%/중앙 +5.3%, 저점지연 22일
    — 글로벌 VIX 신호의 58일 시차 해소). 낙폭 조건은 멜트업(상승 과열 변동성)을
    공포와 구분 — 고점 근처 고변동에선 발동하지 않음. VKOSPI 미수집이면 None.
    """
    try:
        vk = series(con, "VKOSPI", 5)
        ks = series(con, "1001", 260)                 # KOSPI 지수 (52주 창)
    except Exception:                                 # prices_daily 미생성 (테스트/신규 설치)
        return None
    if not vk or len(ks) < 200:
        return None
    v, dd = vk[-1], (ks[-1] / max(ks) - 1) * 100
    if v >= 30 and dd <= -5:
        sig = {"state": "buy", "emoji": "🟢", "cls": "pos",
               "label": "KR 매수 구간 — 로컬 공포",
               "desc": "VKOSPI 30+ & 낙폭 5%+ · 2010~24 승률 75% · 중앙값 +5.3%"}
    elif v >= 30:
        sig = {"state": "hold_melt", "emoji": "🟠", "cls": "hot",
               "label": "KR 보류 — 과열 변동성",
               "desc": "VKOSPI 30+ 이나 낙폭 5% 미달 — 공포 아닌 멜트업 변동성"}
    else:
        sig = {"state": "neutral", "emoji": "⚪", "cls": "",
               "label": "KR 평시 — 신호 없음",
               "desc": "VKOSPI 30 미만"}
    sig.update({"vkospi": v, "kospi_dd": dd, "stale_days": stale_days(con, "VKOSPI")})
    return sig
