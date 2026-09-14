"""Lake errors that map to a specific HTTP status in the web layer."""


class UnsupportedError(Exception):
    """422: the file cannot be served as asked (multi-line CSV, unparseable time column, no
    time column for a ranged download, unknown file type)."""


class LakeUnreachable(RuntimeError):
    """503: the remote lake could not be reached (transport failure). A RuntimeError for
    callers that only know that family, but never a 409 conflict: the terminal route
    answers 503 so clients classify it as transient and retry."""
