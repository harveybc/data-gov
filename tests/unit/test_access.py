from access_plugins.default_access import Plugin
from tests.conftest import PREDICTOR_KEY, SALT, digest


def _plugin():
    p = Plugin()
    p.set_params(
        password_salt=SALT,
        principals={
            "predictor": {
                "kind": "service",
                "api_key_hash": digest(PREDICTOR_KEY),
                "role": "service",
            }
        },
        policies=[
            {
                "principal": "predictor",
                "lake": "financial_files",
                "verbs": ["read"],
                "deny_from": "2025-01-01",
            }
        ],
    )
    return p


def test_password_and_missing_policy():
    access = _plugin()
    assert access.authenticate_password("predictor", "x") is None
    principal = access.authenticate_api_key(PREDICTOR_KEY)
    allow, reason = access.authorize(principal, "no_such_lake", "read")
    assert not allow
    assert reason == "no policy"
    assert access.authenticate_api_key("") is None
    assert access.allowed_lake_ids(None, ["financial_files"]) == []


def test_holdout_policy():
    access = _plugin()
    principal = access.authenticate_api_key(PREDICTOR_KEY)
    allow, reason = access.authorize(
        principal,
        "financial_files",
        "read",
        start="2020-01-01",
        end="2020-02-01",
    )
    assert allow and reason is None
    allow, reason = access.authorize(
        principal,
        "financial_files",
        "read",
        start="2025-01-02",
        end="2025-01-03",
    )
    assert not allow
    assert "holdout" in reason
