import pytest

from app.domain.ids import new_id
from app.domain.pagination import InvalidCursorError, decode_cursor, encode_cursor


def test_cursor_round_trips() -> None:
    lead_id = new_id("ld")

    assert decode_cursor(encode_cursor(lead_id)) == lead_id


@pytest.mark.parametrize("bad", ["", "!!!", encode_cursor("not-a-lead-id"), "bGQ"])
def test_rejects_foreign_cursors(bad: str) -> None:
    with pytest.raises(InvalidCursorError):
        decode_cursor(bad)
