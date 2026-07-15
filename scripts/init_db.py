"""스키마 적용 + 테이블 목록 출력."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import db

con = db.connect()
db.init_schema(con)
tables = [r["name"] for r in con.execute(
    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
)]
print("tables:", ", ".join(tables))
