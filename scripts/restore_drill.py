"""백업 복구 리허설 — '백업이 돈다'와 '백업으로 복구된다'는 다른 말이다.

backup.py는 2026-07-21부터 매일 돌지만 **한 번도 복구해본 적이 없다.** 검증되지 않은
백업은 백업이 아니다. VPS 이관 전에 반드시 한 번은 실제로 되살려봐야 한다.

이 스크립트가 하는 일 (운영 DB는 절대 건드리지 않음 — 임시 폴더에서만 작업):
  1. 최신 zip 선택 → 임시 폴더에 해제
  2. `PRAGMA integrity_check` — 파일이 깨지지 않았나
  3. 운영 DB와 **테이블별 행수 대조** — 백업 시각(보통 06:05) 이후 증가분만큼만 차이나야 정상
  4. 복구본으로 **Flask 앱을 실제 기동**해 주요 라우트 200 확인 (MARKET_HUB_DB 경유)
  5. 소요 시간(RTO) 보고

실행: python scripts/restore_drill.py [--keep]   (--keep: 해제본을 지우지 않음)
"""
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

BACKUP_DIR = ROOT / "backups"
LIVE_DB = ROOT / "data" / "market.db"
ROUTES = ["/", "/us", "/kr", "/leaders", "/positions", "/health",
          "/calendar?tab=earn&view=week"]
KEY_TABLES = ["prices_daily", "analytics_daily", "orders", "signals",
              "portfolio_snapshots", "rotation_slots", "news", "collector_runs"]


def _counts(path: Path) -> dict:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    out = {}
    for t in KEY_TABLES:
        try:
            out[t] = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        except sqlite3.Error:
            out[t] = None
    con.close()
    return out


def main():
    t0 = time.time()
    zips = sorted(BACKUP_DIR.glob("market-hub-*.zip"))
    if not zips:
        sys.exit(f"[실패] 백업이 없습니다: {BACKUP_DIR}")
    z = zips[-1]
    print(f"=== 백업 복구 리허설 ===")
    print(f"  대상: {z.name}  ({z.stat().st_size / 1024 / 1024:.1f} MB · "
          f"보관 {len(zips)}개)")

    work = Path(tempfile.mkdtemp(prefix="mh-restore-"))
    ok = True
    try:
        # ── 1. 해제 ──────────────────────────────────────────────
        with zipfile.ZipFile(z) as f:
            names = f.namelist()
            f.extractall(work)
        print(f"\n  1) 해제: {', '.join(names)}")
        db = work / "market.db"
        if not db.exists():
            sys.exit("[실패] zip 안에 market.db가 없습니다")
        for need in ("settings.toml", ".env"):
            mark = "있음" if (work / need).exists() else "**없음**"
            print(f"     {need}: {mark}")
            ok &= (work / need).exists()

        # ── 2. 무결성 ────────────────────────────────────────────
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        res = con.execute("PRAGMA integrity_check").fetchone()[0]
        ntab = con.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
        con.close()
        print(f"\n  2) 무결성: {res} · 테이블 {ntab}개")
        ok &= (res == "ok")

        # ── 3. 운영 DB와 행수 대조 ───────────────────────────────
        print("\n  3) 운영 DB 대조 (백업 시각 이후 증가분만큼 차이나야 정상)")
        b, live = _counts(db), _counts(LIVE_DB)
        print(f"     {'테이블':<22} {'백업':>12} {'운영':>12} {'차이':>10}")
        for t in KEY_TABLES:
            bv, lv = b[t], live[t]
            if bv is None or lv is None:
                print(f"     {t:<22} {'없음':>12} {'없음' if lv is None else lv:>12}")
                continue
            d = lv - bv
            flag = "  ← 백업이 더 많음(이상)" if d < 0 else ""
            print(f"     {t:<22} {bv:>12,} {lv:>12,} {d:>+10,}{flag}")
            ok &= (d >= 0)

        # ── 4. 복구본으로 실제 기동 ──────────────────────────────
        print("\n  4) 복구본으로 대시보드 기동")
        os.environ["MARKET_HUB_DB"] = str(db)
        os.environ["DASH_PASS"] = ""                  # 리허설용 (시크릿 출력 없음)
        from src.dashboard import create_app

        cl = create_app().test_client()
        for r in ROUTES:
            resp = cl.get(r)
            good = resp.status_code == 200 and len(resp.data) > 500
            ok &= good
            print(f"     {'✓' if good else '✗'} {r:<32} {resp.status_code} "
                  f"{len(resp.data):>7,}B")
    finally:
        if "--keep" in sys.argv:
            print(f"\n  해제본 보존: {work}")
        else:
            shutil.rmtree(work, ignore_errors=True)

    rto = time.time() - t0
    print(f"\n  === 결과: {'성공' if ok else '실패'} · 소요 {rto:.1f}초 (RTO) ===")
    if ok:
        print("  실제 복구 절차: zip 해제 → market.db를 data/에, .env·settings.toml을 제자리에")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
