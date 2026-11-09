"""Lead statuses and the only status changes allowed between them."""

from enum import StrEnum


class LeadStatus(StrEnum):
    RECEIVED = "received"
    QUALIFYING = "qualifying"
    APPROVED = "approved"
    DISQUALIFIED = "disqualified"
    NEEDS_REVIEW = "needs_review"
    REJECTED = "rejected"
    SYNCING = "syncing"
    SYNCED = "synced"
    SYNC_FAILED = "sync_failed"


ALLOWED_TRANSITIONS: dict[LeadStatus, frozenset[LeadStatus]] = {
    LeadStatus.RECEIVED: frozenset({LeadStatus.QUALIFYING}),
    LeadStatus.QUALIFYING: frozenset(
        {LeadStatus.APPROVED, LeadStatus.DISQUALIFIED, LeadStatus.NEEDS_REVIEW}
    ),
    LeadStatus.NEEDS_REVIEW: frozenset(
        {LeadStatus.APPROVED, LeadStatus.REJECTED, LeadStatus.QUALIFYING}
    ),
    LeadStatus.APPROVED: frozenset({LeadStatus.SYNCING}),
    LeadStatus.SYNCING: frozenset({LeadStatus.SYNCED, LeadStatus.SYNC_FAILED}),
    LeadStatus.SYNC_FAILED: frozenset({LeadStatus.SYNCING}),
    LeadStatus.DISQUALIFIED: frozenset(),
    LeadStatus.REJECTED: frozenset(),
    LeadStatus.SYNCED: frozenset(),
}


class InvalidTransitionError(Exception):
    def __init__(self, current: LeadStatus, target: LeadStatus) -> None:
        super().__init__(f"Cannot move a lead from '{current}' to '{target}'.")
        self.current = current
        self.target = target


def check_transition(current: LeadStatus, target: LeadStatus) -> None:
    if target not in ALLOWED_TRANSITIONS[current]:
        raise InvalidTransitionError(current, target)
