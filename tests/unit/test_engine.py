from app.db.engine import create_engine, wait_for_database
from tests.conftest import UNREACHABLE_DATABASE_URL


async def test_wait_for_database_gives_up_after_retries() -> None:
    engine = create_engine(UNREACHABLE_DATABASE_URL)
    try:
        assert await wait_for_database(engine, attempts=2, delay=0) is False
    finally:
        await engine.dispose()
