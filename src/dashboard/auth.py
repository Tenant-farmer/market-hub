"""대시보드 접근 보안 — HTTP Basic Auth. DASH_PASS 설정 시에만 강제.

- DASH_USER/DASH_PASS 미설정: 인증 없음 (로컬 PC 개발용 — 기존 동작 유지)
- 설정 시: 모든 페이지에 Basic Auth. 단 웹훅(/hook/*)은 자체 시크릿이 있어 제외
  (TradingView 서버가 Basic Auth를 못 하므로 — 여기서 막으면 신호가 안 들어옴)

VPS 노출 시: DASH_PASS 설정 + HTTPS(cloudflared 터널/리버스프록시) 경유 권장
(Basic Auth는 평문 HTTP에선 도청 가능 — 터널이 HTTPS를 종단하면 안전).
"""
import hmac
import os

from flask import Response, request


def _ok(user: str, pw: str) -> bool:
    exp_u = os.getenv("DASH_USER", "admin")
    exp_p = os.getenv("DASH_PASS", "")
    return (hmac.compare_digest(user or "", exp_u)
            and hmac.compare_digest(pw or "", exp_p))


def require_auth():
    """before_request 훅. None=통과, Response=차단."""
    if not os.getenv("DASH_PASS"):
        return None                       # 미설정 = 인증 비활성 (로컬)
    if request.path.startswith("/hook"):
        return None                       # 웹훅은 자체 시크릿 (TV는 Basic Auth 불가)
    a = request.authorization
    if a and _ok(a.username, a.password):
        return None
    return Response("인증 필요", 401, {"WWW-Authenticate": 'Basic realm="market-hub"'})
