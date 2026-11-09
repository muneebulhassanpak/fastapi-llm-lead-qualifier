"""Import every model here so Alembic sees the full schema."""

from app.db.models.crm import MockCrmRecord, SyncAttempt
from app.db.models.job import Job
from app.db.models.lead import IdempotencyKey, Lead, LeadEvent
from app.db.models.llm_usage import LlmUsage
from app.db.models.tenant import ApiKey, Tenant
from app.db.models.webhook import WebhookEndpoint

__all__ = [
    "ApiKey",
    "IdempotencyKey",
    "Job",
    "Lead",
    "LeadEvent",
    "LlmUsage",
    "MockCrmRecord",
    "SyncAttempt",
    "Tenant",
    "WebhookEndpoint",
]
