"""What the model must return about a lead, and how that decides the lead's route."""

from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Urgency(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    UNKNOWN = "unknown"


class Qualification(BaseModel):
    """The model's structured answer. Anything that doesn't validate is treated as bad output."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(description="The enquirer's name, if stated.", max_length=200)
    company: str | None = Field(description="The enquirer's company, if stated.", max_length=200)
    intent: str = Field(description="What the enquirer wants, in a few words.", max_length=300)
    budget_hint: str | None = Field(description="Any budget mentioned, as written.", max_length=100)
    urgency: Urgency = Field(description="How soon they need it.")
    score: int = Field(description="Lead quality from 0 (junk) to 100 (ideal).", ge=0, le=100)
    confidence: float = Field(description="Confidence in the score, 0 to 1.", ge=0, le=1)
    reason: str = Field(description="One or two sentences justifying the score.", max_length=500)


class Route(StrEnum):
    APPROVE = "approve"
    DISQUALIFY = "disqualify"
    REVIEW = "review"


@dataclass(frozen=True)
class Thresholds:
    auto_approve_score: int
    disqualify_score: int
    min_confidence: float


@dataclass(frozen=True)
class Decision:
    route: Route
    review_reason: str | None = None


def decide(score: int, confidence: float, thresholds: Thresholds) -> Decision:
    """Only confident, clear-cut scores are acted on automatically; people review the rest."""
    if confidence < thresholds.min_confidence:
        return Decision(Route.REVIEW, "low_confidence")
    if score >= thresholds.auto_approve_score:
        return Decision(Route.APPROVE)
    if score < thresholds.disqualify_score:
        return Decision(Route.DISQUALIFY)
    return Decision(Route.REVIEW, "borderline_score")
