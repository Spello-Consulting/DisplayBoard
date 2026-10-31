"""Tests for blanking stale temperature probe readings in the PowerController topic."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest

from topic_powercontroller import (
    TopicPowerController,
    _is_reading_stale,  # noqa: PLC2701
    _parse_probes,  # noqa: PLC2701
)

TIME_NOW = datetime(2026, 11, 1, 8, 0, tzinfo=UTC)


def _probes_payload(age: timedelta) -> dict[str, Any]:
    reading_time = datetime.now(UTC) - age
    return {
        "TempProbes": [
            {
                "Name": "Temp Pool Water",
                "DisplayName": "Pool",
                "Temperature": 26.14,
                "LastReadingTime": reading_time.isoformat(),
            },
        ]
    }


@pytest.mark.parametrize(
    ("timestamp", "max_age_hours", "expected"),
    [
        ("2026-11-01T07:00:00+00:00", 2, False),  # 1 hour old
        ("2026-11-01T06:00:00+00:00", 2, False),  # exactly at the limit
        ("2026-11-01T05:59:59+00:00", 2, True),  # just over the limit
        ("2026-11-01T16:30:00+11:00", 2, True),  # offsets are honoured: 05:30 UTC
        ("2026-11-01T18:30:00+11:00", 2, False),  # offsets are honoured: 07:30 UTC
        ("2026-11-01T05:00:00+00:00", 0, False),  # zero disables the check
        ("", 2, False),
        (None, 2, False),
        ("garbage", 2, False),
    ],
)
def test_is_reading_stale(timestamp: str | None, max_age_hours: float, expected: bool) -> None:
    """Readings are stale only when a valid timestamp is older than the limit."""
    assert _is_reading_stale(timestamp, max_age_hours, TIME_NOW) is expected


def test_is_reading_stale_naive_timestamp_is_local_time() -> None:
    """A timestamp without a timezone is compared as local time and doesn't raise."""
    time_now = datetime.now().astimezone()
    old = (time_now - timedelta(hours=3)).replace(tzinfo=None).isoformat()
    recent = (time_now - timedelta(hours=1)).replace(tzinfo=None).isoformat()
    assert _is_reading_stale(old, 2, time_now) is True
    assert _is_reading_stale(recent, 2, time_now) is False


def test_parse_probes_keeps_recent_reading() -> None:
    """A recent reading is rounded and passed through."""
    probes = _parse_probes(_probes_payload(timedelta(hours=1)), max_age_hours=2)
    assert probes == [{"name": "Temp Pool Water", "display_name": "Pool", "temperature": 26.1}]


def test_parse_probes_blanks_stale_reading() -> None:
    """A stale reading has its temperature blanked but the probe remains listed."""
    probes = _parse_probes(_probes_payload(timedelta(hours=3)), max_age_hours=2)
    assert probes == [{"name": "Temp Pool Water", "display_name": "Pool", "temperature": None}]


def test_parse_probes_without_last_reading_time() -> None:
    """A probe with no LastReadingTime is still shown."""
    probes = _parse_probes({"TempProbes": [{"Name": "X", "Temperature": 20}]}, max_age_hours=2)
    assert probes[0]["temperature"] == pytest.approx(20.0)


def test_fetch_all_uses_configured_max_age() -> None:
    """The max age passed to the topic controls which probes are blanked."""
    payload = _probes_payload(timedelta(hours=3))
    for max_age_hours, expected in ((2, None), (4, 26.1)):
        topic = TopicPowerController(
            base_url="http://pc", on_update=MagicMock(), logger=MagicMock(), temp_probe_max_age_hours=max_age_hours
        )
        topic._get = lambda endpoint, p=payload: p if endpoint == "/tempprobes" else {}  # type: ignore[method-assign]  # noqa: SLF001
        topic._fetch_all()  # noqa: SLF001
        assert topic.get_data()["powercontroller"]["probes"][0]["temperature"] == expected
