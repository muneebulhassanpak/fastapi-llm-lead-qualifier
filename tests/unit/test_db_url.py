import pytest

from app.db.url import to_async_url

NEON = (
    "postgresql://user:pw@ep-cool-name-123456-pooler.c-2.ap-southeast-1.aws.neon.tech/neondb"
    "?sslmode=require&channel_binding=require"
)


def test_neon_pooled_url_is_made_asyncpg_compatible() -> None:
    db = to_async_url(NEON)

    assert db.url.drivername == "postgresql+asyncpg"
    assert db.url.query == {"ssl": "require", "prepared_statement_cache_size": "0"}
    assert db.connect_args == {"statement_cache_size": 0}


@pytest.mark.parametrize(
    "raw",
    [
        "postgres://u:p@localhost/db",
        "postgresql://u:p@localhost/db",
        "postgresql+asyncpg://u:p@localhost/db",
        "postgresql+psycopg://u:p@localhost/db",
    ],
)
def test_any_postgres_scheme_uses_asyncpg(raw: str) -> None:
    db = to_async_url(raw)

    assert db.url.drivername == "postgresql+asyncpg"
    assert db.connect_args == {}


def test_explicit_ssl_wins_over_sslmode() -> None:
    db = to_async_url("postgresql://u:p@host/db?ssl=verify-full&sslmode=require")

    assert db.url.query == {"ssl": "verify-full"}


def test_rejects_other_databases() -> None:
    with pytest.raises(ValueError, match="Unsupported database backend"):
        to_async_url("mysql://u:p@localhost/db")
