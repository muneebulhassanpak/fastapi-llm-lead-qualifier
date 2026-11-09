from fastapi import APIRouter

from app.api.v1 import health, leads, review, webhooks

api_router = APIRouter(prefix="/v1")
api_router.include_router(health.router)
api_router.include_router(leads.router)
api_router.include_router(review.router)
api_router.include_router(webhooks.router)
