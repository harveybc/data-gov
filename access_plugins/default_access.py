"""Identity and automatic policy in one plugin."""

from __future__ import annotations

import hashlib
import hmac
from datetime import date, datetime


def _parse_day(value):
    if value is None or value == "":
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    text = str(value)[:10]
    return date.fromisoformat(text)


class Plugin:
    plugin_params = {
        "password_salt": "change-me",
        "principals": {},
        "policies": [],
    }

    def __init__(self):
        self.params = dict(self.plugin_params)

    def set_params(self, **kwargs):
        self.params.update(kwargs)

    def _digest(self, secret: str) -> str:
        salt = self.params.get("password_salt") or ""
        return hashlib.sha256(f"{salt}:{secret}".encode()).hexdigest()

    def _match_hash(self, given: str, expected: str) -> bool:
        if not given or not expected:
            return False
        return hmac.compare_digest(given, expected)

    def authenticate_password(self, username: str, password: str):
        record = (self.params.get("principals") or {}).get(username)
        if not record or record.get("kind") != "person":
            return None
        if not self._match_hash(self._digest(password), record.get("password_hash") or ""):
            return None
        return self._principal(username, record)

    def authenticate_api_key(self, api_key: str):
        digest = self._digest(api_key)
        for name, record in (self.params.get("principals") or {}).items():
            if record.get("kind") != "service":
                continue
            if self._match_hash(digest, record.get("api_key_hash") or ""):
                return self._principal(name, record)
        return None

    def _principal(self, username, record):
        return {
            "username": username,
            "display_name": record.get("display_name") or username,
            "role": record.get("role") or "operator",
            "kind": record.get("kind"),
        }

    def authorize(self, principal, lake_id, verb, start=None, end=None):
        if not principal:
            return False, "unauthenticated"
        policies = self.params.get("policies") or []
        matched = False
        deny_from = None
        for policy in policies:
            who = policy.get("principal") or "*"
            if who not in {"*", principal["username"]}:
                continue
            if policy.get("lake") not in {lake_id, "*"}:
                continue
            verbs = policy.get("verbs") or []
            if verb not in verbs and "*" not in verbs:
                continue
            matched = True
            if policy.get("deny_from"):
                deny_from = _parse_day(policy["deny_from"])
        if not matched:
            return False, "no policy"
        start_d, end_d = _parse_day(start), _parse_day(end)
        if deny_from and end_d and end_d >= deny_from:
            return False, "holdout"
        if deny_from and start_d and start_d >= deny_from:
            return False, "holdout"
        return True, None

    def allowed_lake_ids(self, principal, lakes):
        if not principal:
            return []
        allowed = []
        for lake_id in lakes:
            ok, _ = self.authorize(principal, lake_id, "discover")
            if not ok:
                ok, _ = self.authorize(principal, lake_id, "query")
            if ok:
                allowed.append(lake_id)
        return allowed
