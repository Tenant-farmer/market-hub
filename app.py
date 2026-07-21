"""대시보드 실행: python app.py → http://localhost:5000

바인드 주소/포트는 환경변수로 조정: DASH_HOST(기본 127.0.0.1), DASH_PORT(기본 5000).
VPS에서 외부 노출 시 DASH_HOST=0.0.0.0 + DASH_PASS(인증) 설정할 것.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")  # KR 상세(pykrx 로그인) 등에 필요

from src.dashboard import create_app

app = create_app()

if __name__ == "__main__":
    host = os.getenv("DASH_HOST", "127.0.0.1")
    port = int(os.getenv("DASH_PORT", "5000"))
    app.run(host=host, port=port, debug=False)
