"""Event sink for Hermes roles. Skeleton does not call Hermes (no idle tokens)."""


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
