"""Turn any Postgres URL (including Neon's) into one SQLAlchemy + asyncpg accept."""

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.engine import URL, make_url

# libpq query options that asyncpg does not understand.
_UNSUPPORTED_QUERY_KEYS = ("channel_binding", "options", "target_session_attrs")


@dataclass(frozen=True)
class AsyncDatabaseURL:
    url: URL
    connect_args: dict[str, Any] = field(default_factory=dict)


def to_async_url(raw: str) -> AsyncDatabaseURL:
    """Normalise a database URL for asyncpg.

    - `postgres://` / `postgresql://` become `postgresql+asyncpg://`
    - libpq's `sslmode=...` becomes asyncpg's `ssl=...`
    - options asyncpg rejects (e.g. Neon's `channel_binding`) are dropped
    - behind a connection pooler (PgBouncer in transaction mode, e.g. Neon's `-pooler` host)
      prepared-statement caches are turned off, because a cached statement may not exist
      on the next server connection the pooler hands out
    """
    url = make_url(raw)
    if url.get_backend_name() not in ("postgres", "postgresql"):
        raise ValueError(f"Unsupported database backend: {url.get_backend_name()}")
    url = url.set(drivername="postgresql+asyncpg")

    query = dict(url.query)
    sslmode = query.pop("sslmode", None)
    if sslmode is not None and "ssl" not in query:
        query["ssl"] = sslmode
    for key in _UNSUPPORTED_QUERY_KEYS:
        query.pop(key, None)

    connect_args: dict[str, Any] = {}
    if url.host and "-pooler" in url.host:
        query["prepared_statement_cache_size"] = "0"
        connect_args["statement_cache_size"] = 0

    return AsyncDatabaseURL(url=url.set(query=query), connect_args=connect_args)
