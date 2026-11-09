import pytest
from pydantic import ValidationError

from app.domain.qualification import Decision, Qualification, Route, Thresholds, decide

THRESHOLDS = Thresholds(auto_approve_score=60, disqualify_score=30, min_confidence=0.75)


@pytest.mark.parametrize(
    ("score", "confidence", "expected"),
    [
        (60, 0.75, Decision(Route.APPROVE)),
        (100, 1.0, Decision(Route.APPROVE)),
        (59, 0.9, Decision(Route.REVIEW, "borderline_score")),
        (30, 0.9, Decision(Route.REVIEW, "borderline_score")),
        (29, 0.75, Decision(Route.DISQUALIFY)),
        (0, 1.0, Decision(Route.DISQUALIFY)),
        (95, 0.74, Decision(Route.REVIEW, "low_confidence")),
        (5, 0.74, Decision(Route.REVIEW, "low_confidence")),
    ],
)
def test_routing_boundaries(score: int, confidence: float, expected: Decision) -> None:
    assert decide(score, confidence, THRESHOLDS) == expected


def _qualification(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "name": "Sara",
        "company": "Acme",
        "intent": "security audit",
        "budget_hint": "$3k",
        "urgency": "high",
        "score": 82,
        "confidence": 0.9,
        "reason": "Clear need and budget.",
    }
    return {**base, **overrides}


def test_valid_qualification_parses() -> None:
    assert Qualification.model_validate(_qualification()).score == 82


@pytest.mark.parametrize(
    "overrides",
    [
        {"score": 101},
        {"score": -1},
        {"confidence": 1.5},
        {"urgency": "yesterday"},
        {"reason": "x" * 501},
        {"injected": "field"},
    ],
)
def test_out_of_range_output_is_rejected(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Qualification.model_validate(_qualification(**overrides))
