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
        """Propagates LakeUnreachable rather than caching an empty inventory.

        This is deliberate and it is the same rule as the discover route's.  Caching `[]` for a
        lake nobody could reach would put the false green in the CATALOG, where it would outlive
        the outage: every later `resources(lake_id)` would answer "this lake holds nothing" with
        no way left to tell that nobody ever asked it.  A sync that could not reach its lake has
        not synced, so it fails and the previous cache entry, if any, is left untouched.
        """
        lake_id = lake_plugin.params.get("lake_id")
        self.cache[lake_id] = lake_plugin.list_resources()
        return self.cache[lake_id]

    def resources(self, lake_id):
        return self.cache.get(lake_id) or []
