"""Engine and session factory."""

import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.db.url import to_async_url
from app.logging import get_logger

log = get_logger(__name__)


def create_engine(database_url: str) -> AsyncEngine:
    db = to_async_url(database_url)
    return create_async_engine(
        db.url,
        connect_args=db.connect_args,
        # Free-tier Postgres (Neon) drops idle connections when it scales to zero.
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
        pool_recycle=300,
    )


def create_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def ping(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))


async def wait_for_database(engine: AsyncEngine, *, attempts: int = 5, delay: float = 2.0) -> bool:
    """Retry the first connection. A sleeping Neon compute takes a few seconds to wake."""
    for attempt in range(1, attempts + 1):
        try:
            await ping(engine)
        except Exception as exc:  # any driver or network error means "not ready yet"
            log.warning("database_unavailable", attempt=attempt, error=type(exc).__name__)
            if attempt < attempts:
                await asyncio.sleep(delay * attempt)
        else:
            log.info("database_ready", attempt=attempt)
            return True
    return False
