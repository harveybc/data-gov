from lake_plugins.sql_lake import Plugin


def test_select_only(tmp_path):
    import sqlite3

    db = tmp_path / "t.sqlite"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE fact_performance (ts TEXT, value REAL)")
    conn.execute("INSERT INTO fact_performance VALUES ('2024-01-01', 1)")
    conn.commit()
    conn.close()
    lake = Plugin()
    lake.set_params(lake_id="olap_lab", sqlite_path=str(db), time_column="ts")
    names = {item["resource_id"] for item in lake.discover()}
    assert "fact_performance" in names
    out = lake.query("SELECT ts, value FROM fact_performance")
    assert out["rows"][0]["value"] == 1
    try:
        lake.query("DELETE FROM fact_performance")
        assert False, "expected reject"
    except ValueError:
        pass
