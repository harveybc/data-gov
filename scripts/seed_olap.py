#!/usr/bin/env python3
"""Create the lab OLAP sqlite used by examples/config/default.json."""

import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "examples" / "data" / "olap_lab" / "olap.sqlite"


def main():
    DB.parent.mkdir(parents=True, exist_ok=True)
    if DB.exists():
        DB.unlink()
    conn = sqlite3.connect(str(DB))
    conn.execute(
        "CREATE TABLE fact_performance (ts TEXT, experiment_key TEXT, metric TEXT, value REAL)"
    )
    conn.executemany(
        "INSERT INTO fact_performance VALUES (?,?,?,?)",
        [
            ("2024-06-01", "ann_1575", "MAE", 0.01),
            ("2024-07-01", "ann_1575", "MAE", 0.02),
            ("2025-06-01", "ann_1575", "MAE", 0.99),
        ],
    )
    conn.commit()
    conn.close()
    print(f"wrote {DB}")


if __name__ == "__main__":
    main()
