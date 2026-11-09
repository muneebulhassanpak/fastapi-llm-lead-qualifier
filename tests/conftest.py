from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from app.config import AppEnv, Settings
from app.main import create_app

# Nothing listens on port 1, so unit tests never reach a real database.
UNREACHABLE_DATABASE_URL = "postgresql://nobody:nothing@127.0.0.1:1/none"


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env=AppEnv.TEST,
        app_version="9.9.9",
        database_url=UNREACHABLE_DATABASE_URL,
        api_key_pepper=SecretStr("test-pepper"),
    )


@pytest.fixture
async def app(settings: Settings) -> AsyncIterator[FastAPI]:
    app = create_app(settings)
    yield app
    await app.state.engine.dispose()


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
