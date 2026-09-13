"""Automatic allow/deny from config. No human ticket on the hot path."""


class Plugin:
    plugin_params = {
        "default_allow_logged_in": True,
    }

    def __init__(self):
        self.params = dict(self.plugin_params)

    def set_params(self, **kwargs):
        self.params.update(kwargs)

    def allowed_lake_ids(self, user, lakes):
        if not user:
            return []
        if self.params.get("default_allow_logged_in", True):
            return list(lakes)
        allowed = (user.get("lakes") if isinstance(user, dict) else None) or []
        return [lake_id for lake_id in lakes if lake_id in allowed]

    def can(self, user, lake_id, verb="read"):
        if not user:
            return False
        if self.params.get("default_allow_logged_in", True):
            return True
        return lake_id in self.allowed_lake_ids(user, [lake_id])
