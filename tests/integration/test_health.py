from fastapi import FastAPI
from httpx import AsyncClient

from app.config import Settings
from app.db.engine import wait_for_database
from app.main import create_app


async def test_health_checks_database(client: AsyncClient) -> None:
    response = await client.get("/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "db": "ok", "version": "9.9.9"}


async def test_app_lifespan_connects_and_disposes(app: FastAPI) -> None:
    async with app.router.lifespan_context(app):
        assert await wait_for_database(app.state.engine, attempts=1)


async def test_lifespan_starts_and_stops_the_worker(settings: Settings) -> None:
    app = create_app(settings.model_copy(update={"worker_enabled": True}))

    async with app.router.lifespan_context(app):
        task = app.state.worker_task
        assert task is not None
        assert not task.done()

    assert task.done()
    assert task.exception() is None
