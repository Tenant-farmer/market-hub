"""market-hub 프로세스 전수 점검 — 고아·중복·미등록 프로세스 색출.

판정 기준
- 작업(schtasks)마다 실행 스크립트를 XML에서 읽어 매칭 키로 쓴다 (손매칭 금지)
- venv 런처(pythonw.exe) → 실제 인터프리터(pythonw3.12.exe) 부모-자식 쌍은 **1개**로 센다
- 고아 = 부모가 스케줄러(svchost)도 아니고 살아있는 python도 아닌 프로세스
- 중복 = 한 작업에 실행 체인이 2개 이상
- 미등록 = market-hub 경로를 도는 python인데 어느 작업 스크립트에도 안 걸리는 것

주의: 조회 명령줄에 검색어를 그대로 쓰면 **자기 자신이 잡힌다**(2026-07-28에 두 번 당함).
python 프로세스만 보고 자기 PID를 제외하는 이유.

사용: python scripts/audit_procs.py
"""
import json
import os
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(r"C:\Users\user\Desktop\github\market-hub")
SELF = os.getpid()


def ps(cmd):
    r = subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                       capture_output=True, text=True, timeout=120)
    return r.stdout


def tasks():
    out = ps("schtasks /Query /FO CSV /NH | ConvertFrom-Csv -Header n,t,s | "
             "Where-Object { $_.n -like '*market-hub*' } | ForEach-Object { $_.n + '|' + $_.s }")
    res = {}
    for line in out.splitlines():
        if "|" not in line:
            continue
        name, state = line.rsplit("|", 1)
        name = name.strip().lstrip("\\")
        xml = subprocess.run(["schtasks", "/Query", "/TN", f"\\{name}", "/XML"],
                             capture_output=True, text=True, timeout=30).stdout
        script = ""
        if "<?xml" in xml:
            root = ET.fromstring(xml[xml.index("<?xml"):])
            ns = {"t": root.tag.split("}")[0].strip("{")} if "}" in root.tag else {}
            a = root.find(".//t:Exec/t:Arguments", ns) if ns else root.find(".//Exec/Arguments")
            script = (a.text or "").strip() if a is not None else ""
        res[name] = {"state": state.strip(), "script": script}
    return res


def procs():
    """market-hub를 도는 python 프로세스 전부 (자기 자신·조회용 셸 제외)."""
    out = ps("Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'python*' } | "
             "Select-Object ProcessId,ParentProcessId,Name,CommandLine,"
             "@{n='St';e={$_.CreationDate.ToString('MM-dd HH:mm:ss')}} | ConvertTo-Json -Depth 2")
    try:
        data = json.loads(out) if out.strip() else []
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        data = [data]
    return [p for p in data
            if p.get("CommandLine") and "market-hub" in p["CommandLine"]
            and p["ProcessId"] != SELF and "audit_procs" not in p["CommandLine"]]


def main():
    tk, pr = tasks(), procs()
    alive = {p["ProcessId"] for p in pr}
    print(f"market-hub 파이썬 프로세스 {len(pr)}개 / 등록 작업 {len(tk)}개\n")

    claimed, issues = set(), []
    for name, meta in sorted(tk.items()):
        key = Path(meta["script"]).name if meta["script"] else ""
        mine = [p for p in pr if key and key in p["CommandLine"]] if key else []
        claimed |= {p["ProcessId"] for p in mine}
        # 부모가 이 그룹 안에 있으면 자식 → 체인의 뿌리만 센다
        roots = [p for p in mine if p["ParentProcessId"] not in {q["ProcessId"] for q in mine}]

        print(f"■ {name}   [작업상태 {meta['state']}]")
        print(f"   스크립트: {key or '(정의 없음)'}")
        if not mine:
            print("   프로세스: 없음")
        for r in roots:
            kids = [p for p in mine if p["ParentProcessId"] == r["ProcessId"]]
            par_alive = r["ParentProcessId"] in alive
            print(f"   체인 PID {r['ProcessId']}"
                  f"{' → ' + ','.join(str(k['ProcessId']) for k in kids) if kids else ''}"
                  f"   시작 {r['St']}   부모PID {r['ParentProcessId']}"
                  f"{' (살아있는 python)' if par_alive else ''}")
        if len(roots) > 1:
            issues.append(f"{name}: 실행 체인 {len(roots)}개 — 중복 실행")
        print()

    stray = [p for p in pr if p["ProcessId"] not in claimed]
    if stray:
        print("■ 어느 작업에도 안 걸리는 프로세스")
        for p in stray:
            cmd = p["CommandLine"]
            print(f"   PID {p['ProcessId']}  시작 {p['St']}  부모 {p['ParentProcessId']}")
            print(f"      {cmd[:150]}")
            issues.append(f"미등록: PID {p['ProcessId']} {Path(cmd.split()[-1]).name}")
        print()

    print("=" * 60)
    if issues:
        print("발견된 문제:")
        for i in issues:
            print("  ❌", i)
    else:
        print("✅ 고아·중복·미등록 없음")


main()
