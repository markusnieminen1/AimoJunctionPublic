"""Prefixed ULID generation helpers."""

from ulid import ULID


def new_id(prefix: str) -> str:
    """Return a lexicographically sortable prefixed ULID (e.g. cmp_01J...)."""
    normalized = prefix if prefix.endswith("_") else f"{prefix}_"
    return f"{normalized}{ULID()}"
