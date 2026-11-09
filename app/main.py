"""Application factory. Run with `uvicorn app.main:app`."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.v1.router import api_router
from app.config import AppEnv, Settings, get_settings
from app.db.engine import create_engine, create_sessionmaker, wait_for_database
from app.errors import ErrorResponse, register_error_handlers
from app.logging import configure_logging, get_logger
from app.middleware import RequestContextMiddleware

DESCRIPTION = """
Scores incoming sales enquiries with an LLM, routes uncertain ones to a human review queue,
and syncs approved leads to a CRM with retries and idempotency.

**Authentication:** send your API key in the `X-API-Key` header.

**Demo project:** this is a personal portfolio project. Use fake data only.
"""

OPENAPI_TAGS = [
    {"name": "health", "description": "Liveness and dependency checks."},
    {"name": "leads", "description": "Submit enquiries and read their qualification results."},
]


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, json=settings.app_env is not AppEnv.DEVELOPMENT)
    log = get_logger(__name__)
    engine = create_engine(settings.database_url)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        log.info("startup", env=settings.app_env, version=settings.app_version)
        # Start even if the database is still waking up; /v1/health reports it until it is.
        await wait_for_database(engine)
        yield
        await engine.dispose()
        log.info("shutdown")

    app = FastAPI(
        title="Lead Qualification API",
        description=DESCRIPTION,
        version=settings.app_version,
        license_info={"name": "MIT", "identifier": "MIT"},
        contact={
            "name": "Muhammad Muneeb-Ul-Hassan",
            "url": "https://github.com/muneebulhassanpak",
        },
        openapi_tags=OPENAPI_TAGS,
        responses={500: {"model": ErrorResponse, "description": "Unexpected server error"}},
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.sessionmaker = create_sessionmaker(engine)
    app.add_middleware(RequestContextMiddleware)
    register_error_handlers(app)
    app.include_router(api_router)
    return app


app = create_app()
