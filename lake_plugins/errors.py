"""Lake errors that map to a specific HTTP status in the web layer."""


class UnsupportedError(Exception):
    """422: the file cannot be served as asked (multi-line CSV, unparseable time column, no
    time column for a ranged download, unknown file type)."""
