"""대시보드 표시용 상수/포맷."""

QUAD = {1: "Leading", 2: "Weakening", 3: "Lagging", 4: "Improving"}
QUAD_KO = {1: "주도", 2: "약화", 3: "침체", 4: "개선"}
QUAD_DESC = {
    1: "주도 사분면",
    2: "약화 사분면 · 힘 빠지는 중",
    3: "침체 사분면",
    4: "개선 사분면 · 진입 중",
}
ACTION_KO = {"new": "신규", "add": "확대", "trim": "축소", "exit": "청산"}
INV_KO = {"foreign": "외국인", "institution": "기관", "individual": "개인"}


def fng_label(v: float) -> str:
    if v < 25:
        return "극단적 공포"
    if v < 45:
        return "공포"
    if v <= 55:
        return "중립"
    if v <= 75:
        return "탐욕"
    return "극단적 탐욕"


def fmt_krw(v: float) -> str:
    a = abs(v)
    if a >= 1e12:
        return f"{v / 1e12:,.1f}조"
    if a >= 1e8:
        return f"{v / 1e8:,.0f}억"
    return f"{v / 1e4:,.0f}만"


def fmt_usd(v: float) -> str:
    a = abs(v)
    if a >= 1e12:
        return f"${v / 1e12:,.2f}T"
    if a >= 1e9:
        return f"${v / 1e9:,.1f}B"
    if a >= 1e6:
        return f"${v / 1e6:,.0f}M"
    return f"${v / 1e3:,.0f}K"
