"""Shared FastAPI dependencies."""

from typing import Annotated, cast

from fastapi import Depends, Request

from app.config import Settings


def get_app_settings(request: Request) -> Settings:
    """Settings the running app was created with (not the process-wide default)."""
    return cast(Settings, request.app.state.settings)


SettingsDep = Annotated[Settings, Depends(get_app_settings)]
