"""Local directory lake: inventory of files + host disk usage."""

from __future__ import annotations

import hashlib
from pathlib import Path
import shutil


class Plugin:
    plugin_params = {
        "lake_id": "default",
        "title": "Unnamed lake",
        "description": "Local file lake (skeleton).",
        "root_path": "./examples/data/financial_files",
        "kind": "files_inventory",
    }

    def __init__(self):
        self.params = dict(self.plugin_params)

    def set_params(self, **kwargs):
        self.params.update(kwargs)

    def _root(self) -> Path:
        return Path(self.params.get("root_path") or ".").resolve()

    def list_resources(self):
        root = self._root()
        if not root.exists():
            return []
        items = []
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.name.startswith("."):
                continue
            digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
            items.append(
                {
                    "resource_id": str(path.relative_to(root)),
                    "bytes": path.stat().st_size,
                    "sha256_prefix": digest,
                    "kind": "file",
                }
            )
        return items

    def storage(self):
        root = self._root()
        root.mkdir(parents=True, exist_ok=True)
        usage = shutil.disk_usage(root)
        used_in_lake = 0
        if root.exists():
            used_in_lake = sum(
                p.stat().st_size for p in root.rglob("*") if p.is_file()
            )
        return {
            "root": str(root),
            "host_total": usage.total,
            "host_used": usage.used,
            "host_free": usage.free,
            "lake_bytes": used_in_lake,
        }

    def describe(self):
        return {
            "lake_id": self.params.get("lake_id"),
            "title": self.params.get("title"),
            "description": self.params.get("description"),
            "kind": self.params.get("kind"),
            "root_path": str(self._root()),
        }
