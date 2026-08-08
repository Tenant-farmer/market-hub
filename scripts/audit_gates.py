"""환경변수 게이트 감사 — **테스트가 그 안으로 들어가는가**.

왜 필요한가 (2026-08-08): `if os.getenv("TELEGRAM_BOT_TOKEN"):` 안쪽에서 `morning`
변수를 놓쳐 NameError가 263회 났고 **8/6 1차 판정이 발송되지 않았다**. 그런데
"테스트 90개 통과"였다 — 라우팅 테스트가 그 환경변수를 **지우고** 돌아 블록에
아예 진입하지 않았기 때문이다.

**"테스트가 통과했다"는 "그 코드가 실행됐다"를 뜻하지 않는다.**

이 스크립트는 src의 환경변수 게이트를 모두 찾아, 테스트가 그 변수를 설정하는지 본다.
설정하지 않으면 그 안쪽은 한 번도 실행된 적이 없다는 뜻이다(정적 근사 — 커버리지
도구가 아니지만 의존성 추가 없이 같은 함정을 잡는다).

사용: python scripts/audit_gates.py     (구멍이 있으면 exit 1)
"""
import collections
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]

# 테스트가 설정하지 않아도 되는 것들 — 이유를 명시해야 면제된다
EXEMPT = {
    "PYTEST_CURRENT_TEST": "pytest가 자동 설정 — 기록 경로는 test_errlog_writes_*가 별도 검증",
}


def main() -> int:
    gates = collections.defaultdict(list)
    for f in (ROOT / "src").rglob("*.py"):
        if "__pycache__" in str(f):
            continue
        for i, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            for m in re.finditer(r'getenv\(["\'](\w+)["\']', line):
                if re.match(r"\s*(if|elif)\s", line) or " if " in line:
                    gates[m.group(1)].append(f"{f.relative_to(ROOT)}:{i}")

    tests = "".join(p.read_text(encoding="utf-8", errors="replace")
                    for p in (ROOT / "tests").glob("*.py"))

    def _covered(k: str) -> bool:
        """테스트가 이 변수를 **켜는가**.

        `setenv("K", ...)` 리터럴만 보면 딕셔너리로 넘기는 헬퍼(`{"EXIT_ENABLED": "1"}`)를
        놓친다. 반대로 이름만 나오면 되게 하면 `delenv`(끄기)까지 통과해버린다.
        → 이름이 등장하되 **delenv 전용이 아닌** 줄이 하나라도 있어야 한다.
        """
        hit = False
        for line in tests.splitlines():
            if f'"{k}"' not in line and f"'{k}'" not in line:
                continue
            if "delenv" in line and "setenv" not in line:
                continue                       # 끄기만 하는 줄은 진입 근거가 아니다
            hit = True
        return hit

    holes = []
    print(f"환경변수 게이트 {len(gates)}종")
    for k in sorted(gates):
        covered = _covered(k)
        if k in EXEMPT:
            print(f"  {k:<24} {len(gates[k])}곳  ⊘ 면제 — {EXEMPT[k]}")
            continue
        print(f"  {k:<24} {len(gates[k])}곳  {'✅' if covered else '❌ 테스트 미진입'}")
        if not covered:
            holes.append(k)
            for loc in gates[k]:
                print(f"       {loc}")

    print()
    if holes:
        print(f"❌ 테스트가 진입하지 않는 게이트 {len(holes)}종: {', '.join(holes)}")
        print("   → 해당 변수를 setenv로 켠 뒤 그 안쪽 로직이 실행되는지 확인하는 테스트를 추가할 것")
        return 1
    print("✅ 모든 게이트가 테스트로 진입됨")
    return 0


if __name__ == "__main__":
    sys.exit(main())
