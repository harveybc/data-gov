"""Store classification independent of transport and policy.

Legacy engine names remain accepted on input. An untyped HTTP endpoint is
unclassified, not implicitly a file lake.
"""

LEGACY_KINDS = {"files_inventory": "lake", "sql_olap": "warehouse"}


def store_metadata(params, *, adapter_kind=None, adapter_engine=None, remote=None):
    value = params.get("kind")
    if value is not None and (not isinstance(value, str) or value not in (
        "lake", "warehouse", "http", *LEGACY_KINDS
    )):
        raise ValueError("store kind must be lake or warehouse")
    kind = LEGACY_KINDS.get(value, value)
    if kind == "http":
        kind = None
    if adapter_kind and kind and kind != adapter_kind:
        raise ValueError(f"store kind {kind} conflicts with adapter kind {adapter_kind}")
    remote = remote or {}
    remote_value = remote.get("kind")
    remote_kind = LEGACY_KINDS.get(remote_value) if isinstance(remote_value, str) else None
    if remote_value in ("lake", "warehouse"):
        remote_kind = remote_value
    kind = kind or adapter_kind or remote_kind
    engine = params.get("engine") or adapter_engine
    if not engine and value in LEGACY_KINDS:
        engine = value
    # A configured classification wins over a stale remote description.
    if not engine and remote_kind is not None and remote_kind == kind:
        engine = remote.get("engine") or (remote_value if remote_value in LEGACY_KINDS else None)
    return {"kind": kind, "engine": engine,
            "transport": "local" if adapter_kind else "http"}
