from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api.deps import SettingsDep

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: Literal["ok"] = Field(description="Overall service status.")
    version: str = Field(description="Running application version.", examples=["1.0.0"])


@router.get(
    "/health",
    summary="Service health",
    description=(
        "Returns 200 when the service is up. No authentication. "
        "Used by the hosting platform's health check and by uptime monitors."
    ),
)
async def health(settings: SettingsDep) -> HealthResponse:
    return HealthResponse(status="ok", version=settings.app_version)
