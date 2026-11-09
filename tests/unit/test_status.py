import pytest

from app.domain.status import (
    ALLOWED_TRANSITIONS,
    InvalidTransitionError,
    LeadStatus,
    check_transition,
)


def test_every_status_has_a_transition_entry() -> None:
    assert set(ALLOWED_TRANSITIONS) == set(LeadStatus)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (LeadStatus.RECEIVED, LeadStatus.QUALIFYING),
        (LeadStatus.QUALIFYING, LeadStatus.APPROVED),
        (LeadStatus.QUALIFYING, LeadStatus.NEEDS_REVIEW),
        (LeadStatus.NEEDS_REVIEW, LeadStatus.REJECTED),
        (LeadStatus.APPROVED, LeadStatus.SYNCING),
        (LeadStatus.SYNCING, LeadStatus.SYNC_FAILED),
        (LeadStatus.SYNC_FAILED, LeadStatus.SYNCING),
    ],
)
def test_allowed_transitions(current: LeadStatus, target: LeadStatus) -> None:
    check_transition(current, target)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (LeadStatus.RECEIVED, LeadStatus.SYNCED),
        (LeadStatus.REJECTED, LeadStatus.APPROVED),
        (LeadStatus.SYNCED, LeadStatus.SYNCING),
        (LeadStatus.DISQUALIFIED, LeadStatus.APPROVED),
        (LeadStatus.APPROVED, LeadStatus.APPROVED),
    ],
)
def test_forbidden_transitions(current: LeadStatus, target: LeadStatus) -> None:
    with pytest.raises(InvalidTransitionError) as exc_info:
        check_transition(current, target)

    assert exc_info.value.current is current
    assert exc_info.value.target is target


@pytest.mark.parametrize(
    "terminal", [LeadStatus.REJECTED, LeadStatus.DISQUALIFIED, LeadStatus.SYNCED]
)
def test_terminal_statuses_have_no_way_out(terminal: LeadStatus) -> None:
    assert ALLOWED_TRANSITIONS[terminal] == frozenset()
