from accounting_plugins.default_accounting import Plugin


def test_usage_filter(tmp_path):
    acc = Plugin()
    acc.set_params(accounting_db=str(tmp_path / "a.db"))
    acc.record(
        actor="predictor",
        lake_id="l",
        verb="read",
        decision="allow",
        experiment_key="e1",
        sha256="h",
    )
    acc.record(
        actor="doin",
        lake_id="l",
        verb="read",
        decision="allow",
        experiment_key="e2",
        sha256="i",
    )
    rows = acc.usage("e1")
    assert len(rows) == 1
    assert rows[0]["actor"] == "predictor"
