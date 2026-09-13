"""Dispatch governance events. No Hermes call unless hermes_enabled and an event exists.

`source_changed` fires inline at download time (web layer, indexed comparison of the last
download). `scan_accounting` stays as an offline, read-only sweep over a window of rows."""

import json


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

    @staticmethod
    def _source_sha256(row):
        try:
            return (json.loads(row.get("detail") or "{}") or {}).get("source_sha256")
        except ValueError:
            return None

    def scan_accounting(self, accounting):
        rows = accounting.logs_all(limit=1000)
        last_hash = {}
        last_source = {}
        for row in reversed(rows):
            if row.get("decision") != "allow":
                continue
            key = (row.get("lake_id"), row.get("resource_id"))
            if row.get("verb") == "read":
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
            elif row.get("verb") == "download":
                source = self._source_sha256(row)
                previous = last_source.get(key)
                if previous and source and previous != source:
                    self.handle_event(
                        {
                            "kind": "source_changed",
                            "lake_id": row.get("lake_id"),
                            "resource_id": row.get("resource_id"),
                            "previous_source_sha256": previous,
                            "source_sha256": source,
                        }
                    )
                if source:
                    last_source[key] = source
        return list(self.events)
