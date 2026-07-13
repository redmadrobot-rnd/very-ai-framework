"""backstop.status — маппинг пробы в цвет индикатора (чистая логика)."""

from __future__ import annotations

import pytest

from srv_explore import backstop


@pytest.mark.parametrize(
    "fs, egress, expected",
    [
        (True, True, "green"),
        (True, False, "green"),
        (True, None, "green"),
        (False, True, "red"),
        (False, None, "red"),
        (None, None, "unknown"),
        (None, True, "unknown"),
    ],
)
def test_status(fs, egress, expected) -> None:
    assert backstop.status({"fs_readonly": fs, "egress_locked": egress}) == expected
