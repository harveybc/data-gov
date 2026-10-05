"""Load plugins from setuptools entry-point groups (importlib.metadata)."""

from importlib.metadata import entry_points


def load_plugin(plugin_group: str, plugin_name: str):
    try:
        selected = entry_points(group=plugin_group)
        match = next((ep for ep in selected if ep.name == plugin_name), None)
        if match is None:
            raise ImportError(
                f"Plugin {plugin_name!r} not found in group {plugin_group!r}."
            )
        plugin_class = match.load()
        required_params = list(getattr(plugin_class, "plugin_params", {}).keys())
        return plugin_class, required_params
    except ImportError:
        raise
    except Exception as exc:
        raise ImportError(
            f"Failed to load plugin {plugin_name!r} from {plugin_group!r}: {exc}"
        ) from exc


def get_plugin_params(plugin_group: str, plugin_name: str):
    plugin_class, _ = load_plugin(plugin_group, plugin_name)
    return dict(getattr(plugin_class, "plugin_params", {}))
