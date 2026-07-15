"""KRX(pykrx) 공용부 — 로그인 게이트 (kr_sectors / kr_flows 공유)."""
import os


def require_login() -> None:
    if not (os.getenv("KRX_ID") and os.getenv("KRX_PW")):
        raise RuntimeError(
            "KRX_ID/KRX_PW 미설정 — data.krx.co.kr 무료 계정 생성 후 .env에 기입하세요"
        )
