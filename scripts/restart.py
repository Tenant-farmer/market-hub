"""서비스 재시작 — 작업 스케줄러 정의에서 **실제 스크립트 경로를 읽어** 그 프로세스만 잡는다.

왜 스크립트가 됐나 (2026-07-28 사고): 손으로 친
`Where-Object { $_.CommandLine -match 'dashboard' }` 가 **한 번도 대시보드를 못 죽였다**.
작업이 돌리는 건 `app.py`라 'dashboard' 문자열이 명령줄에 없었기 때문. 그 결과
- 옛 프로세스가 포트 5000을 계속 쥐고 있었고
- `schtasks /Run`으로 뜬 새 인스턴스는 바인드 실패로 즉사(Last Result 오류)
- **코드를 고치고 "재시작했다"고 믿는 동안 옛 코드가 돌고 있었다**

패턴을 손으로 짐작하면 또 어긋난다 → 작업 XML의 Arguments(=스크립트 경로)를
그대로 매칭 키로 쓴다. 작업 정의가 바뀌면 이 스크립트도 저절로 따라간다.

사용: python scripts/restart.py dashboard|engine|tunnel|all
"""
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TASKS = {"dashboard": "market-hub-dashboard", "engine": "market-hub-engine",
         "tunnel": "market-hub-tunnel", "hourly": "market-hub-hourly"}
PORT = {"dashboard": 5000}          # 바인드까지 확인할 서비스


def _ps(cmd: str) -> str:
    r = subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                       capture_output=True, text=True, timeout=60)
    return r.stdout.strip()


def _script_path(task: str) -> str:
    """작업이 실행하는 스크립트 경로 — 이게 프로세스 매칭 키다."""
    xml = subprocess.run(["schtasks", "/Query", "/TN", f"\\{task}", "/XML"],
                         capture_output=True, text=True, timeout=30).stdout
    # schtasks가 UTF-16 BOM을 붙여 나오는 경우가 있어 선두 잡음을 잘라낸다
    xml = xml[xml.index("<?xml"):] if "<?xml" in xml else xml
    root = ET.fromstring(xml)
    ns = {"t": root.tag.split("}")[0].strip("{")} if "}" in root.tag else {}
    arg = root.find(".//t:Exec/t:Arguments", ns) if ns else root.find(".//Exec/Arguments")
    return (arg.text or "").strip() if arg is not None else ""


def _pids(script: str) -> list[int]:
    """그 스크립트를 돌리는 파이썬 프로세스 — 스케줄러가 띄운 것도, 고아도 전부."""
    if not script:
        return []
    name = Path(script).name          # app.py / worker 실행줄 등
    out = _ps(f"Get-CimInstance Win32_Process | Where-Object {{ $_.Name -like 'python*' "
              f"-and $_.CommandLine -like '*{name}*' }} | "
              f"ForEach-Object {{ $_.ProcessId }}")
    return [int(x) for x in out.split() if x.isdigit()]


def restart(key: str) -> bool:
    task = TASKS[key]
    script = _script_path(task)
    olds = _pids(script)
    print(f"[{key}] 대상 스크립트: {script or '(없음)'}")
    print(f"[{key}] 기존 프로세스: {olds or '없음'}")
    for pid in olds:
        _ps(f"Stop-Process -Id {pid} -Force -ErrorAction SilentlyContinue")
    subprocess.run(["schtasks", "/End", "/TN", f"\\{task}"], capture_output=True, timeout=30)
    subprocess.run(["schtasks", "/Run", "/TN", f"\\{task}"], capture_output=True, timeout=30)

    port = PORT.get(key)
    for _ in range(15):               # 기동까지 최대 ~15초 대기
        time.sleep(1)
        new = [p for p in _pids(script) if p not in olds]
        if not new:
            continue
        if port is None:
            print(f"[{key}] 기동 OK — PID {new}")
            return True
        owner = _ps(f"(Get-NetTCPConnection -LocalPort {port} -State Listen "
                    f"-ErrorAction SilentlyContinue).OwningProcess")
        if owner.strip():
            print(f"[{key}] 기동 OK — PID {new}, 포트 {port} 점유 PID {owner.strip()}")
            return True
    print(f"[{key}] ❌ 기동 확인 실패 — 수동 확인 필요")
    return False


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "dashboard"
    keys = list(TASKS) if which == "all" else [which]
    bad = [k for k in keys if k in TASKS and not restart(k)]
    sys.exit(1 if bad else 0)
