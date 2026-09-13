"""Local username/password from the JSON config. Skeleton only."""


class Plugin:
    plugin_params = {
        "users": {
            "demo": {
                "password": "demo",
                "display_name": "Demo operator",
                "role": "operator",
            }
        }
    }

    def __init__(self):
        self.params = dict(self.plugin_params)

    def set_params(self, **kwargs):
        self.params.update(kwargs)

    def authenticate(self, username: str, password: str):
        record = (self.params.get("users") or {}).get(username)
        if not record or str(record.get("password")) != str(password):
            return None
        return {
            "username": username,
            "display_name": record.get("display_name") or username,
            "role": record.get("role") or "operator",
        }
