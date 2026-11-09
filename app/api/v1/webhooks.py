"""Inbound webhooks: signed deliveries from outside services become leads."""

import time
from collections.abc import Mapping
from datetime import timedelta
from http import HTTPStatus
from typing import Annotated

from fastapi import APIRouter, Path, Request, Response, status

from app.api.deps import SessionDep, SettingsDep
from app.api.v1.schemas import LeadAccepted
from app.db.models.webhook import WebhookProvider
from app.errors import AppError, ErrorResponse
from app.services import leads as lead_service
from app.services import webhooks as webhook_service
from app.webhooks.base import InvalidPayloadError, WebhookAdapter
from app.webhooks.generic import GenericAdapter
from app.webhooks.secrets import decrypt_secret
from app.webhooks.tally import TallyAdapter

MAX_BODY_BYTES = 64 * 1024
# Longer than API idempotency: Tally keeps retrying a failed delivery for about 31 hours.
DEDUP_WINDOW = timedelta(hours=72)

ADAPTERS: Mapping[WebhookProvider, WebhookAdapter] = {
    WebhookProvider.GENERIC: GenericAdapter(),
    WebhookProvider.TALLY: TallyAdapter(),
}


class InvalidSignatureError(AppError):
    # One answer for every failed check, so a caller learns nothing about which one failed.
    status_code = HTTPStatus.UNAUTHORIZED
    code = "invalid_signature"


class PayloadTooLargeError(AppError):
    status_code = HTTPStatus.REQUEST_ENTITY_TOO_LARGE
    code = "payload_too_large"


class InvalidWebhookPayloadError(AppError):
    status_code = HTTPStatus.UNPROCESSABLE_ENTITY
    code = "validation_error"


router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post(
    "/{provider}/{endpoint_id}",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Receive a signed webhook",
    description=(
        "For outside services, not API clients: authenticated by the request signature, not "
        "an API key. Create the endpoint and its secret with "
        "`python -m app.cli create-webhook`.\n\n"
        "The lead goes through the same pipeline as `POST /v1/leads`. A repeated delivery "
        "(same provider event ID) returns `200` with the original lead and creates nothing."
    ),
    responses={
        200: {"model": LeadAccepted, "description": "Repeat of an earlier delivery"},
        401: {"model": ErrorResponse, "description": "Unknown endpoint or invalid signature"},
        409: {"model": ErrorResponse, "description": "Event ID reused with another payload"},
        413: {"model": ErrorResponse, "description": f"Body over {MAX_BODY_BYTES} bytes"},
        422: {"model": ErrorResponse, "description": "Signed, but not a usable lead"},
    },
)
async def receive_webhook(
    provider: WebhookProvider,
    endpoint_id: Annotated[str, Path(max_length=32)],
    request: Request,
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
) -> LeadAccepted:
    body = await _read_body(request)
    endpoint = await webhook_service.get_active_endpoint(session, endpoint_id, provider)
    adapter = ADAPTERS[provider]
    if endpoint is None:
        raise InvalidSignatureError("Invalid webhook signature.")
    secret = decrypt_secret(
        endpoint.secret_encrypted, settings.webhook_secret_key.get_secret_value()
    )
    if not adapter.verify(secret, body, request.headers, now=time.time()):
        raise InvalidSignatureError("Invalid webhook signature.")
    try:
        inbound = adapter.parse(body)
    except InvalidPayloadError as exc:
        raise InvalidWebhookPayloadError(exc.message, details=exc.details) from exc

    result = await lead_service.create_lead(
        session,
        endpoint.tenant_id,
        inbound.lead,
        actor=f"webhook:{endpoint.id}",
        idempotency_key=f"webhook:{endpoint.id}:{inbound.event_id}",
        idempotency_ttl=DEDUP_WINDOW,
        job_max_attempts=settings.job_max_attempts,
    )
    if result.replayed:
        response.status_code = status.HTTP_200_OK
    return LeadAccepted.from_model(result.lead)


async def _read_body(request: Request) -> bytes:
    """The raw body, as signed. Stops reading as soon as it is over the limit."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise PayloadTooLargeError(f"Webhook bodies are limited to {MAX_BODY_BYTES} bytes.")
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            raise PayloadTooLargeError(f"Webhook bodies are limited to {MAX_BODY_BYTES} bytes.")
        chunks.append(chunk)
    return b"".join(chunks)
