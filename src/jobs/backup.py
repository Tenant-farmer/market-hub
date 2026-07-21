"""로컬 백업 — market.db 스냅샷 + 설정(.env, settings.toml)을 zip으로 보관.

- SQLite 백업 API 사용: 대시보드/수집기가 DB를 쓰는 중에도 안전
- backups/ 아래 market-hub-YYYYMMDD-HHMM.zip, 최근 14개만 유지
- hourly 아침 슬롯에서 하루 1회 실행 (collector_runs 'backup' → /health에서 확인)
- 복원: zip을 풀어 market.db를 data/에, 설정 파일을 제자리에 두면 끝
- VPS 이전 시에도 그대로 사용 (호스팅 AUTO BACKUP 유료 기능 대체)
"""
import sqlite3
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKUP_DIR = ROOT / "backups"
KEEP = 14


def run(con=None) -> int:
    """백업 1회 실행. 반환값은 zip 크기(KB) — collector_runs rows에 기록됨."""
    BACKUP_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    tmp_db = BACKUP_DIR / f"market-{stamp}.db"

    src = sqlite3.connect(ROOT / "data" / "market.db")
    dst = sqlite3.connect(tmp_db)
    with dst:
        src.backup(dst)
    dst.close()
    src.close()

    zip_path = BACKUP_DIR / f"market-hub-{stamp}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(tmp_db, "market.db")
        for extra in (ROOT / "config" / "settings.toml", ROOT / ".env"):
            if extra.exists():
                z.write(extra, extra.name)
    tmp_db.unlink()

    for old in sorted(BACKUP_DIR.glob("market-hub-*.zip"))[:-KEEP]:
        old.unlink()

    return int(zip_path.stat().st_size / 1024)


if __name__ == "__main__":
    print(f"backup: {run()} KB")
