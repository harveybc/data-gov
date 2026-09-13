"""Alias of files_lake so older configs keep working."""

from lake_plugins.files_lake import Plugin as FilesPlugin


class Plugin(FilesPlugin):
    pass
