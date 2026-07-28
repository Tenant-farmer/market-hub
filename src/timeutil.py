"""시각 변환 — 경제 캘린더의 `gmt` 필드는 이름과 달리 **미 동부시간(ET)**이다.

2026-07-29 실측으로 확인:
- Nasdaq 응답의 `gmt=08:30` 항목이 Core PCE인데, Core PCE는 **08:30 ET** 발표다
- `gmt=14:00`은 FOMC Statement — FOMC도 **14:00 ET**다
- ET→KST 변환(여름 +13h)하면 21:30 KST가 나오고, 이는 외부 시황 서비스 표기와 일치한다

우리는 이걸 **+9h(=UTC 가정)**로 변환하고 있었다. 결과:
- /econ 탭·주간 다이제스트의 시각이 4시간 이르게 표시
- `event_alerts`가 발표 시각을 4시간 일찍 잡아 **값이 안 나온 상태에서 알림**을 시도

여름·겨울(EDT/EST)에 따라 13h·14h로 달라지므로 고정 오프셋이 아니라 zoneinfo를 쓴다.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
KST = ZoneInfo("Asia/Seoul")


def et_to_kst(date_str: str, hhmm: str) -> datetime | None:
    """캘린더의 (날짜, gmt) → KST datetime. 파싱 불가면 None."""
    if not (date_str and hhmm):
        return None
    try:
        return datetime.fromisoformat(f"{date_str} {hhmm}").replace(tzinfo=ET).astimezone(KST)
    except ValueError:
        return None


def kst_now() -> datetime:
    return datetime.now(KST)
