"""A deterministic, offline stand-in for an LLM. Used in tests and as the demo default.

It scores with simple keyword rules, so results are predictable and cost nothing. It is not
meant to be clever, only to exercise every route (approve, disqualify, review) realistically.
"""

import re

from app.domain.qualification import Qualification, Urgency
from app.llm.base import LeadInput, QualificationResult
from app.llm.prompt import PROMPT_VERSION

_INJECTION = re.compile(
    r"ignore (all |any |the )?(previous|prior|above) instructions|"
    r"you are now|system prompt|score (this|me|it) \d+|disregard (your|the) (rules|instructions)",
    re.IGNORECASE,
)
_SPAM = re.compile(r"\b(seo services|crypto|casino|backlinks|guest post|we offer)\b", re.IGNORECASE)
_BUDGET = re.compile(
    r"(\$|usd|€|£)\s?\d[\d,.]*\s?k?|\d[\d,.]*\s?k?\s?(\$|usd|dollars|euros?)", re.IGNORECASE
)
_HIGH_URGENCY = re.compile(r"\b(asap|urgent|this week|next week|before launch|deadline)\b", re.I)
_MEDIUM_URGENCY = re.compile(r"\b(next month|this quarter|in \d+ (weeks|months))\b", re.I)
_PROJECT = re.compile(
    r"\b(app|api|website|platform|integration|audit|mvp|dashboard|chatbot|ai|backend)\b", re.I
)


class MockProvider:
    name = "mock"
    model = "mock-rules-v1"

    async def qualify(self, lead: LeadInput) -> QualificationResult:
        return QualificationResult(
            qualification=self._score(lead),
            provider=self.name,
            model=self.model,
            prompt_version=PROMPT_VERSION,
        )

    def _score(self, lead: LeadInput) -> Qualification:
        text = lead.message
        budget = _BUDGET.search(text)
        urgency = (
            Urgency.HIGH
            if _HIGH_URGENCY.search(text)
            else Urgency.MEDIUM
            if _MEDIUM_URGENCY.search(text)
            else Urgency.UNKNOWN
        )

        def result(score: int, confidence: float, reason: str) -> Qualification:
            return Qualification(
                name=lead.name,
                company=None,
                intent=text[:80],
                budget_hint=budget.group(0).strip() if budget else None,
                urgency=urgency,
                score=min(score, 100),
                confidence=confidence,
                reason=reason,
            )

        if _INJECTION.search(text):
            return result(5, 0.95, "The message tries to instruct the scoring system: abuse.")
        if _SPAM.search(text):
            return result(10, 0.9, "Reads as a sales pitch or spam.")
        if len(text) < 25:
            return result(40, 0.5, "Too short to judge the need.")

        score = 35
        signals = []
        if _PROJECT.search(text):
            score += 20
            signals.append("a concrete project")
        if budget:
            score += 20
            signals.append(f"a budget ({budget.group(0).strip()})")
        if urgency is not Urgency.UNKNOWN:
            score += 10
            signals.append(f"{urgency.value} urgency")
        reason = "Mentions " + ", ".join(signals) + "." if signals else "No clear project signals."
        return result(score, 0.85, reason)
