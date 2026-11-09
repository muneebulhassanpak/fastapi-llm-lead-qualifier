from fastapi import FastAPI
from httpx import AsyncClient

from app.db.engine import wait_for_database


async def test_health_checks_database(client: AsyncClient) -> None:
    response = await client.get("/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "db": "ok", "version": "9.9.9"}


async def test_app_lifespan_connects_and_disposes(app: FastAPI) -> None:
    async with app.router.lifespan_context(app):
        assert await wait_for_database(app.state.engine, attempts=1)
