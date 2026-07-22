"""backstop.status/net_status — маппинг проб в цвет индикатора (чистая логика)."""

from __future__ import annotations

import pytest

from srv_explore import backstop


@pytest.mark.parametrize(
    "val, expected",
    [
        (True, "green"),
        (False, "red"),
        (None, "unknown"),
    ],
)
def test_status(val, expected) -> None:
    assert backstop.status({"fs_readonly": val}) == expected


@pytest.mark.parametrize(
    "val, expected",
    [
        (True, "green"),
        (False, "red"),
        (None, "unknown"),
    ],
)
def test_net_status(val, expected) -> None:
    assert backstop.net_status({"egress_locked": val}) == expected
