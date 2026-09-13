"""Pull resource lists from each lake adapter into an in-memory catalog."""


class Plugin:
    plugin_params = {
        "inventory_cache": {},
    }

    def __init__(self):
        self.params = dict(self.plugin_params)
        self.cache = {}

    def set_params(self, **kwargs):
        self.params.update(kwargs)

    def sync_lake(self, lake_plugin):
        lake_id = lake_plugin.params.get("lake_id")
        self.cache[lake_id] = lake_plugin.list_resources()
        return self.cache[lake_id]

    def resources(self, lake_id):
        return self.cache.get(lake_id) or []
