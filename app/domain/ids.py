"""Prefixed, time-sortable IDs, e.g. `ld_01J9Z3K4...`."""

from ulid import ULID


def new_id(prefix: str) -> str:
    return f"{prefix}_{ULID()}"
