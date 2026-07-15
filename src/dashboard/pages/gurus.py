"""구루 13F 페이지."""
from flask import Blueprint, render_template

from src import db
from src.dashboard.fmt import ACTION_KO, fmt_usd

bp = Blueprint("gurus", __name__)


@bp.get("/gurus")
def gurus_page():
    con = db.connect()
    managers = []
    for f in con.execute(
        """
        SELECT cik, manager_name, quarter, filed_date, accession
        FROM guru_filings gf
        WHERE quarter = (SELECT MAX(quarter) FROM guru_filings WHERE cik = gf.cik)
        ORDER BY manager_name
        """
    ).fetchall():
        holdings = [
            {"name": h["name"], "pct": h["pct"], "value": fmt_usd(h["value_usd"])}
            for h in con.execute(
                "SELECT name, pct, value_usd FROM guru_holdings WHERE accession=? "
                "ORDER BY pct DESC LIMIT 8",
                (f["accession"],),
            )
        ]
        changes = [
            {"name": c["name"], "action": c["action"], "action_ko": ACTION_KO[c["action"]],
             "delta": fmt_usd(c["delta_value"])}
            for c in con.execute(
                "SELECT name, action, delta_value FROM guru_changes WHERE cik=? AND quarter=? "
                "ORDER BY ABS(delta_value) DESC LIMIT 6",
                (f["cik"], f["quarter"]),
            )
        ]
        managers.append({
            "name": f["manager_name"], "quarter": f["quarter"],
            "filed": f["filed_date"], "holdings": holdings, "changes": changes,
        })

    consensus = [
        {"name": r["name"], "n": r["n"], "delta": fmt_usd(r["dv"]), "who": r["who"]}
        for r in con.execute(
            """
            SELECT c.name, COUNT(DISTINCT c.cik) n, SUM(c.delta_value) dv,
                   GROUP_CONCAT(DISTINCT f.manager_name) who
            FROM guru_changes c
            JOIN (SELECT DISTINCT cik, manager_name FROM guru_filings) f ON f.cik = c.cik
            WHERE c.action IN ('new', 'add')
            GROUP BY c.name HAVING n >= 2
            ORDER BY n DESC, dv DESC LIMIT 10
            """
        )
    ]
    con.close()
    return render_template("gurus.html", managers=managers, consensus=consensus)
