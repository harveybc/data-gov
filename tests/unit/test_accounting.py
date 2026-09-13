import json
import sqlite3

from accounting_plugins.default_accounting import Plugin


def _acc(tmp_path):
    acc = Plugin()
    acc.set_params(accounting_db=str(tmp_path / "a.db"))
    return acc


def _download(acc, sha, actor="predictor", key="e1", lake="l", resource="r", source="s1"):
    return acc.record(
        actor=actor,
        lake_id=lake,
        verb="download",
        resource_id=resource,
        decision="allow",
        experiment_key=key,
        sha256=sha,
        bytes=3,
        detail=json.dumps({"source_sha256": source, "delivery": "AS_IS"}),
    )


def test_usage_filter(tmp_path):
    acc = _acc(tmp_path)
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
    assert isinstance(rows[0]["id"], int)


def test_indexes_exist(tmp_path):
    acc = _acc(tmp_path)
    acc.record(verb="authn")
    conn = sqlite3.connect(str(tmp_path / "a.db"))
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    conn.close()
    assert {"ix_events_sha256", "ix_events_experiment", "ix_events_lake_resource"} <= names


def test_last_and_find_download(tmp_path):
    acc = _acc(tmp_path)
    first = _download(acc, "aaa", source="s1")
    assert acc.last_download("l", "r")["id"] == first
    acc.record(actor="predictor", lake_id="l", verb="download", resource_id="r", decision="deny")
    assert acc.last_download("l", "r")["id"] == first
    second = _download(acc, "bbb", key="e2", source="s2")
    assert acc.last_download("l", "r")["id"] == second
    assert acc.last_download("l", "other") is None
    assert acc.find_download("l", "r", "aaa", "predictor", ["e1"])["id"] == first
    assert acc.find_download("l", "r", "aaa", "predictor", ["e9", "e1"])["id"] == first
    assert acc.find_download("l", "r", "aaa", "predictor", ["e2"]) is None
    assert acc.find_download("l", "r", "aaa", "doin", ["e1"]) is None
    assert acc.find_download("l", "x", "aaa", "predictor", ["e1"]) is None
    assert acc.find_download("l", "r", "aaa", "predictor", []) is None


def test_usage_paging_and_by_sha256(tmp_path):
    acc = _acc(tmp_path)
    ids = [_download(acc, "aaa", key="e1") for _ in range(5)]
    acc.record(actor="predictor", lake_id="l", verb="read", decision="allow", sha256="aaa", experiment_key="e1")
    page = acc.usage("e1", limit=2)
    assert [r["id"] for r in page] == [ids[-1] + 1, ids[-1]]
    older = acc.usage("e1", limit=2, before_id=page[-1]["id"])
    assert [r["id"] for r in older] == [ids[-2], ids[-3]]
    by_sha = acc.usage_by_sha256("aaa")
    assert [r["id"] for r in by_sha] == list(reversed(ids))  # allow downloads only, not the read
    assert acc.usage_by_sha256("aaa", limit=1, before_id=ids[1]) [0]["id"] == ids[0]
