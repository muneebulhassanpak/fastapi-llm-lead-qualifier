"""Tally form submissions (https://tally.so/help/webhooks).

Tally signs the body with `Tally-Signature` and sends every answer as a list of fields.
The lead's message is the form's long-text answers followed by every other answer as
"Label: value" lines, so the model sees things like budget and timeline. An email field and a
text field labelled like "name" fill `email` and `name`.
"""

import re
from collections.abc import Iterable, Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field, TypeAdapter, ValidationError

from app.services.leads import NewLead
from app.webhooks.base import MAX_EVENT_ID_LENGTH, InboundLead, InvalidPayloadError
from app.webhooks.signatures import verify_tally

SOURCE = "tally"
MAX_MESSAGE_LENGTH = 5000
MAX_NAME_LENGTH = 200
MAX_ID_LENGTH = 100
_EMAIL: TypeAdapter[str] = TypeAdapter(EmailStr)
_NAME_LABEL = re.compile(r"\bname\b", re.IGNORECASE)


class TallyOption(BaseModel):
    id: str
    text: str


class TallyField(BaseModel):
    key: str
    label: str | None = None
    type: str
    value: Any = None
    options: list[TallyOption] | None = None


class TallyData(BaseModel):
    response_id: str = Field(alias="responseId")
    form_id: str = Field(alias="formId")
    form_name: str | None = Field(default=None, alias="formName")
    fields: list[TallyField]


class TallyPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    event_id: str = Field(alias="eventId", min_length=1, max_length=MAX_EVENT_ID_LENGTH)
    event_type: str = Field(alias="eventType")
    data: TallyData


class TallyAdapter:
    def verify(self, secret: str, body: bytes, headers: Mapping[str, str], *, now: float) -> bool:
        return verify_tally(secret, body, headers.get("tally-signature"))

    def parse(self, body: bytes) -> InboundLead:
        try:
            payload = TallyPayload.model_validate_json(body)
        except ValidationError as exc:
            raise InvalidPayloadError("Not a Tally webhook payload.") from exc
        if payload.event_type != "FORM_RESPONSE":
            raise InvalidPayloadError(f"Unsupported Tally event '{payload.event_type}'.")

        fields = payload.data.fields
        email = _first(f for f in fields if f.type == "INPUT_EMAIL")
        name = _first(
            f for f in fields if f.type == "INPUT_TEXT" and _NAME_LABEL.search(f.label or "")
        )
        texts = [_text(f) for f in fields if f.type == "TEXTAREA"]
        answers = [
            f"{field.label or field.key}: {_text(field)}"
            for field in fields
            if field.type != "TEXTAREA" and field not in (email, name) and _text(field)
        ]
        message = "\n\n".join(filter(None, ["\n\n".join(filter(None, texts)), "\n".join(answers)]))
        if not message:
            raise InvalidPayloadError("The form response has no answers to qualify.")

        ids = {
            "form_id": payload.data.form_id,
            "form_name": payload.data.form_name,
            "response_id": payload.data.response_id,
        }
        metadata = {"tally": {k: v[:MAX_ID_LENGTH] for k, v in ids.items() if v}}
        return InboundLead(
            event_id=payload.event_id,
            lead=NewLead(
                message=message[:MAX_MESSAGE_LENGTH],
                source=SOURCE,
                name=_text(name)[:MAX_NAME_LENGTH] if name else None,
                email=_valid_email(_text(email)) if email else None,
                metadata=metadata,
            ),
        )


def _valid_email(value: str) -> str | None:
    try:
        return str(_EMAIL.validate_python(value))
    except ValidationError:
        return None  # still in the form, just not usable as the lead's email


def _first(fields: Iterable[TallyField]) -> TallyField | None:
    return next((f for f in fields if _text(f)), None)


def _text(field: TallyField) -> str:
    """The answer as plain text: choice IDs become their labels; lists are comma-joined."""
    value = field.value
    if value is None or value == "" or value == []:
        return ""
    if isinstance(value, list):
        labels = {o.id: o.text for o in field.options or []}
        parts = [labels.get(v, v) if isinstance(v, str) else _scalar(v) for v in value]
        return ", ".join(p for p in parts if p)
    return _scalar(value).strip()


def _scalar(value: Any) -> str:
    if isinstance(value, str | int | float | bool):
        return str(value)
    return ""  # files, signatures, matrices: nothing useful to qualify on
