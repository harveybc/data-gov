"""Startup rules of app.main: holdout lakes need deny_from policies; the spool is swept."""

import pytest

from app.main import check_startup, sweep_spool


def test_holdout_lake_without_deny_from_refuses_to_start():
    config = {
        "lakes": [{"lake_id": "files", "holdout_start": "2025-01-01"}],
        "policies": [{"principal": "*", "lake": "files", "verbs": ["download"]}],
    }
    with pytest.raises(SystemExit, match="deny_from"):
        check_startup(config)
    config["policies"][0]["deny_from"] = "2025-01-01"
    check_startup(config)


def test_wildcard_policy_and_lakes_without_holdout():
    check_startup(
        {
            "lakes": [{"lake_id": "cube"}, {"lake_id": "files", "holdout_start": "2025-01-01"}],
            "policies": [{"principal": "*", "lake": "cube", "verbs": ["query"]}],
        }
    )
    with pytest.raises(SystemExit):
        check_startup(
            {
                "lakes": [{"lake_id": "files", "holdout_start": "2025-01-01"}],
                "policies": [{"principal": "*", "lake": "*", "verbs": ["*"]}],
            }
        )


def test_sweep_spool_removes_leftovers(tmp_path):
    spool = tmp_path / "spool"
    spool.mkdir()
    (spool / "a.part").write_bytes(b"x")
    (spool / "b.part").write_bytes(b"y")
    (spool / "keep").mkdir()
    assert sweep_spool(spool) == 2
    assert [p.name for p in spool.iterdir()] == ["keep"]
    assert sweep_spool(tmp_path / "fresh") == 0 and (tmp_path / "fresh").is_dir()
