"""Dispatch governance events. No Hermes call unless hermes_enabled and an event exists."""


class Plugin:
    plugin_params = {
        "hermes_enabled": False,
    }

    def __init__(self):
        self.params = dict(self.plugin_params)
        self.events = []

    def set_params(self, **kwargs):
        self.params.update(kwargs)

    def handle_event(self, event: dict):
        self.events.append(event)
        return None

    def scan_accounting(self, accounting):
        rows = accounting.logs_all(limit=1000)
        last_hash = {}
        for row in reversed(rows):
            if row.get("verb") != "read" or row.get("decision") != "allow":
                continue
            key = (row.get("lake_id"), row.get("resource_id"))
            digest = row.get("sha256")
            if key in last_hash and last_hash[key] and digest and last_hash[key] != digest:
                self.handle_event(
                    {
                        "kind": "hash_mismatch",
                        "lake_id": row.get("lake_id"),
                        "resource_id": row.get("resource_id"),
                    }
                )
            if digest:
                last_hash[key] = digest
        return list(self.events)
