"""대시보드 공유 터널 유지 — localhost.run ssh 역터널 (사용자가 끄라고 할 때까지).

- 끊기면 10초 후 자동 재접속. 무료 티어라 재접속 시 URL이 바뀔 수 있음 →
  새 URL을 텔레그램으로 통지 (친구에게 다시 공유)
- 보안: DASH_PASS 필수 (Basic Auth) — 이 스크립트는 비번 미설정이면 기동 거부
- 중지: schtasks /End /TN market-hub-tunnel + 프로세스 kill (docs/UNATTENDED.md)
"""
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")
import os  # noqa: E402

LOG = ROOT / "data" / "tunnel.log"


def _log(msg: str):
    line = f"[tunnel] {datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _notify(text: str):
    try:
        from src import notify

        notify.send(text)
    except Exception:
        pass


def main():
    if not os.getenv("DASH_PASS"):
        _log("DASH_PASS 미설정 — 공개 노출 위험, 기동 거부")
        return
    last_url = None
    while True:
        _log("ssh 터널 접속 시도")
        try:
            p = subprocess.Popen(
                ["ssh", "-tt",                        # 배너(URL)는 TTY 있어야 출력됨
                 "-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=20",
                 "-o", "ServerAliveInterval=30", "-o", "ServerAliveCountMax=3",
                 "-o", "ExitOnForwardFailure=yes",
                 "-R", "80:localhost:5000", "nokey@localhost.run"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace")
            for line in p.stdout:
                m = re.search(r"https://\S+\.lhr\.life", line)
                if m and m.group(0) != last_url:
                    last_url = m.group(0)
                    _log(f"URL: {last_url}")
                    _notify(f"🌐 대시보드 공유 주소: {last_url}\n"
                            f"(아이디 admin · 비밀번호는 따로 전달한 것)")
            p.wait()
        except Exception as e:
            _log(f"오류: {str(e)[:80]}")
        _log("터널 종료 — 60초 후 재접속 (잦은 재시도는 스로틀 유발)")
        time.sleep(60)


if __name__ == "__main__":
    main()
