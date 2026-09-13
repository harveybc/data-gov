"""Default pipeline: seed demo accounting, then serve the web UI."""


class Plugin:
    plugin_params = {
        "pipeline_plugin": "default_pipeline",
        "seed_demo_logs": True,
    }
    plugin_debug_vars = ["seed_demo_logs"]

    def __init__(self):
        self.params = dict(self.plugin_params)

    def set_params(self, **kwargs):
        self.params.update(kwargs)

    def run(self, context):
        plugins = context["plugins"]
        accounting = plugins["accounting"]
        if self.params.get("seed_demo_logs"):
            accounting.seed_demo_if_empty(list(plugins["lakes"]))
        for lake in plugins["lakes"].values():
            plugins["inventory"].sync_lake(lake)
        return plugins["web"].serve(context)
