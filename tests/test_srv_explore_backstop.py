"""backstop.status — маппинг пробы в цвет индикатора (чистая логика)."""

from __future__ import annotations

import pytest

from srv_explore import backstop


@pytest.mark.parametrize(
    "fs, expected",
    [
        (True, "green"),
        (False, "red"),
        (None, "unknown"),
    ],
)
def test_status(fs, expected) -> None:
    assert backstop.status({"fs_readonly": fs}) == expected
