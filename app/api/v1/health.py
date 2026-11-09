from typing import Literal

from fastapi import APIRouter, Response, status
from pydantic import BaseModel, Field

from app.api.deps import EngineDep, SettingsDep
from app.db.engine import ping
from app.logging import get_logger

router = APIRouter(tags=["health"])
log = get_logger(__name__)


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"] = Field(description="Overall service status.")
    db: Literal["ok", "error"] = Field(description="Whether the database answered.")
    version: str = Field(description="Running application version.", examples=["1.0.0"])


@router.get(
    "/health",
    summary="Service health",
    description=(
        "Returns 200 when the service and its database are up, 503 otherwise. "
        "No authentication. Used by the hosting platform's health check and uptime monitors; "
        "the database query also wakes a sleeping serverless database."
    ),
    responses={503: {"model": HealthResponse, "description": "Database unreachable"}},
)
async def health(settings: SettingsDep, engine: EngineDep, response: Response) -> HealthResponse:
    try:
        await ping(engine)
    except Exception as exc:  # report any failure as "db down" rather than a 500
        log.warning("health_db_error", error=type(exc).__name__)
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return HealthResponse(status="degraded", db="error", version=settings.app_version)
    return HealthResponse(status="ok", db="ok", version=settings.app_version)
