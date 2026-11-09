"""Background loop that deletes old demo data, when DEMO_RETENTION_DAYS is set."""

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.logging import get_logger
from app.services.cleanup import delete_old_data

log = get_logger(__name__)

CLEANUP_INTERVAL_SECONDS = 3600.0


async def run_cleanup(
    sessionmaker: async_sessionmaker[AsyncSession],
    retention: timedelta,
    stop: asyncio.Event,
    *,
    interval: float = CLEANUP_INTERVAL_SECONDS,
) -> None:
    """Delete data older than `retention` now, then every `interval` seconds until `stop`."""
    log.info("cleanup_started", retention_days=retention.days)
    while not stop.is_set():
        try:
            async with sessionmaker() as session:
                result = await delete_old_data(session, datetime.now(UTC) - retention)
            if result.leads or result.crm_records:
                log.info("old_data_deleted", leads=result.leads, crm_records=result.crm_records)
        except Exception:
            log.exception("cleanup_failed")  # try again next time
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval)
