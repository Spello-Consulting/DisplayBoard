"""Tests for UPS support in the PowerController topic."""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
import requests

from topic_powercontroller import (
    TopicPowerController,
    _format_runtime,  # noqa: PLC2701
    _parse_ups,  # noqa: PLC2701
    _select_ups,  # noqa: PLC2701
)

SAMPLE_UPS: dict[str, Any] = {
    "UPS": [
        {
            "Timestamp": "2026-10-13T08:48:50",
            "Timestamp__datatype": "datetime",
            "UPSName": "Eaton 5E UPS",
            "BatteryChargePercent": 81,
            "BatteryRuntimeSeconds": 1100,
            "BatteryState": "charging",
            "IsHealthy": True,
        },
        {
            "Timestamp": "2026-10-13T08:48:50",
            "UPSName": "Garage UPS",
            "BatteryChargePercent": 100,
            "BatteryRuntimeSeconds": 4500,
            "BatteryState": "full",
            "IsHealthy": False,
        },
    ]
}


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(None, "—"), (-1, "—"), (0, "0m"), (1100, "18m"), (3600, "1h 00m"), (4500, "1h 15m")],
)
def test_format_runtime(seconds: int | None, expected: str) -> None:
    """Runtime seconds are formatted as hours/minutes."""
    assert _format_runtime(seconds) == expected


def test_parse_ups_sample() -> None:
    """The sample DataAPI payload is normalised correctly."""
    ups = _parse_ups(SAMPLE_UPS)
    assert len(ups) == 2
    assert ups[0] == {
        "name": "Eaton 5E UPS",
        "timestamp": "2026-10-13T08:48:50",
        "time": "8:48 AM",
        "charge_percent": 81,
        "runtime_sec": 1100,
        "runtime": "18m",
        "battery_state": "charging",
        "is_healthy": True,
    }
    assert ups[1]["is_healthy"] is False


def test_parse_ups_missing_fields() -> None:
    """Missing/null fields don't raise and produce safe defaults."""
    ups = _parse_ups({"UPS": [{"UPSName": "X", "BatteryChargePercent": None, "Timestamp": "garbage"}]})
    assert ups[0]["charge_percent"] is None
    assert ups[0]["runtime_sec"] is None
    assert ups[0]["runtime"] == "—"
    assert not ups[0]["time"]
    assert not ups[0]["battery_state"]
    assert ups[0]["is_healthy"] is False


def test_parse_ups_empty() -> None:
    """An empty or absent UPS list yields an empty list."""
    assert _parse_ups({}) == []
    assert _parse_ups({"UPS": []}) == []


def test_select_ups() -> None:
    """The configured UPS is selected by case-insensitive name; first UPS if unset."""
    ups = _parse_ups(SAMPLE_UPS)
    assert _select_ups(ups, "garage ups") == ups[1]
    assert _select_ups(ups, None) == ups[0]
    assert _select_ups(ups, "") == ups[0]
    assert _select_ups(ups, "Unknown") is None
    assert _select_ups([], "Eaton 5E UPS") is None


def _make_topic(responses: dict[str, Any], ups_name: str | None = "Garage UPS") -> tuple[TopicPowerController, MagicMock]:
    logger = MagicMock()
    topic = TopicPowerController(
        base_url="http://pc", on_update=MagicMock(), logger=logger, ups_name=ups_name
    )

    def fake_get(endpoint: str) -> Any:
        value = responses.get(endpoint, {})
        if isinstance(value, Exception):
            raise value
        return value

    topic._get = fake_get  # type: ignore[method-assign]  # noqa: SLF001
    return topic, logger


def test_fetch_all_includes_ups() -> None:
    """_fetch_all adds the ups list and the selected UPS to the powercontroller data."""
    topic, _ = _make_topic({"/ups": SAMPLE_UPS})
    topic._fetch_all()  # noqa: SLF001
    pc = topic.get_data()["powercontroller"]
    assert pc["connected"] is True
    assert len(pc["ups"]) == 2
    assert pc["ups_selected"]["name"] == "Garage UPS"


def test_fetch_all_ups_endpoint_missing() -> None:
    """An older PowerController without /ups still connects; UPS data is empty."""
    topic, logger = _make_topic({"/ups": requests.exceptions.HTTPError("404 Not Found")})
    topic._fetch_all()  # noqa: SLF001
    topic._fetch_all()  # noqa: SLF001
    pc = topic.get_data()["powercontroller"]
    assert pc["connected"] is True
    assert pc["ups"] == []
    assert pc["ups_selected"] is None
    assert logger.log_message.call_count == 1  # warned only once


def test_fetch_all_ups_name_not_found_warns_once() -> None:
    """A configured UPSName that isn't present logs a single warning."""
    topic, logger = _make_topic({"/ups": SAMPLE_UPS}, ups_name="Nope")
    topic._fetch_all()  # noqa: SLF001
    topic._fetch_all()  # noqa: SLF001
    assert topic.get_data()["powercontroller"]["ups_selected"] is None
    assert logger.log_message.call_count == 1
