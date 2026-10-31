"""PowerController topic — polls the PC DataAPI for outputs, meters, probes, UPS status, and energy prices."""
from __future__ import annotations

import threading
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

import requests
from sc_foundation import DateHelper

if TYPE_CHECKING:
    from collections.abc import Callable

    from sc_foundation import SCLogger

# Map Amber/PC energy price status values to normalised display classes.
# PC DataAPI passes through Amber status strings.
_STATUS_NORMALISE: dict[str, str] = {
    "spike":   "spike",
    "high":    "high",
    "neutral": "medium",
    "ok":      "low",
    "low":     "low",
    "verylow": "low",
}

# Map normalised energy status to display icons.
_STATUS_ICONS: dict[str, str] = {
    "spike":  "⛔️",
    "high":   "🛑",
    "medium": "⚠️",
    "low":    " ",
}


def _normalise_status(raw: str | None) -> str:
    if not raw:
        return "medium"
    return _STATUS_NORMALISE.get(raw.lower().replace(" ", ""), "medium")


def _parse_outputs(data: dict) -> list[dict]:
    results = []
    for item in data.get("Outputs", []):
        results.append({
            "name": item.get("Name", ""),
            "display_name": item.get("DisplayName", item.get("Name", "")),
            "state": item.get("State", "").upper() == "ON",
            "mode": item.get("AppMode", ""),
        })
    return results


def _parse_meters(data: dict) -> list[dict]:
    results = []
    for item in data.get("Meters", []):
        power_raw = item.get("Power", 0)
        results.append({
            "name": item.get("Name", ""),
            "display_name": item.get("DisplayName", item.get("Name", "")),
            "power_w": round(float(power_raw), 1) if power_raw is not None else 0.0,
        })
    return results


def _is_reading_stale(timestamp_raw: str | None, max_age_hours: float, time_now: datetime) -> bool:
    """Decide whether a temperature probe reading is too old to display.

    Args:
        timestamp_raw: The ISO format time of the reading. A timestamp without a timezone is taken as local time.
        max_age_hours: The maximum age of a reading in hours. Zero disables the check.
        time_now: The current timezone aware time.

    Returns:
        True if the reading is older than max_age_hours. A missing or unparseable timestamp is not treated as stale.
    """
    if not max_age_hours or not timestamp_raw:
        return False
    try:
        reading_time = datetime.fromisoformat(timestamp_raw).astimezone()
    except (ValueError, TypeError):
        return False
    return time_now - reading_time > timedelta(hours=max_age_hours)


def _parse_probes(data: dict[str, Any], max_age_hours: float = 0) -> list[dict[str, Any]]:
    """Parse the /tempprobes DataAPI response into a list of probe dicts.

    Args:
        data: The JSON response from the /tempprobes endpoint, containing a "TempProbes" list.
        max_age_hours: Blank the temperature of probes whose latest reading is older than this. Zero disables the check.

    Returns:
        A list of normalised probe dicts. The temperature is None if there is no current reading.
    """
    results: list[dict[str, Any]] = []
    time_now = DateHelper.now()
    for item in data.get("TempProbes", []):
        temp_raw = item.get("Temperature")
        if _is_reading_stale(item.get("LastReadingTime"), max_age_hours, time_now):
            temp_raw = None
        results.append({
            "name": item.get("Name", ""),
            "display_name": item.get("DisplayName", item.get("Name", "")),
            "temperature": round(float(temp_raw), 1) if temp_raw is not None else None,
        })
    return results


def _format_runtime(seconds: int | None) -> str:
    """Format a battery runtime in seconds as a short human-readable string.

    Args:
        seconds: Runtime in seconds, or None if unknown.

    Returns:
        A string such as "18m", "1h 05m", or "—" if unknown.
    """
    if seconds is None or seconds < 0:
        return "—"
    hours, remainder = divmod(int(seconds), 3600)
    minutes = remainder // 60
    if hours:
        return f"{hours}h {minutes:02d}m"
    return f"{minutes}m"


def _parse_ups(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Parse the /ups DataAPI response into a list of UPS dicts.

    Args:
        data: The JSON response from the /ups endpoint, containing a "UPS" list.

    Returns:
        A list of normalised UPS dicts.
    """
    results: list[dict[str, Any]] = []
    for item in data.get("UPS", []):
        charge_raw = item.get("BatteryChargePercent")
        runtime_raw = item.get("BatteryRuntimeSeconds")
        runtime_sec = int(runtime_raw) if runtime_raw is not None else None
        timestamp_raw = item.get("Timestamp", "")
        display_time = ""
        if timestamp_raw:
            try:
                display_time = datetime.fromisoformat(timestamp_raw).strftime("%I:%M %p").lstrip("0")
            except (ValueError, TypeError):
                display_time = ""
        results.append({
            "name": item.get("UPSName", ""),
            "timestamp": timestamp_raw,
            "time": display_time,
            "charge_percent": round(float(charge_raw)) if charge_raw is not None else None,
            "runtime_sec": runtime_sec,
            "runtime": _format_runtime(runtime_sec),
            "battery_state": str(item.get("BatteryState", "") or ""),
            "is_healthy": bool(item.get("IsHealthy", False)),
        })
    return results


def _select_ups(ups_list: list[dict[str, Any]], ups_name: str | None) -> dict[str, Any] | None:
    """Pick the configured UPS from the list.

    Args:
        ups_list: Parsed UPS dicts.
        ups_name: The configured UPS name. If None/empty, the first UPS is used.

    Returns:
        The matching UPS dict, or None if there is no match.
    """
    if not ups_list:
        return None
    if not ups_name:
        return ups_list[0]
    for ups in ups_list:
        if ups["name"].casefold() == ups_name.casefold():
            return ups
    return None


def _parse_energy_prices(data: dict) -> tuple[dict, list[dict]]:
    """Return (current_price_dict, forecast_list)."""
    current: dict = {}
    forecast: list[dict] = []
    time_now = DateHelper.now()

    for item in data.get("EnergyPrices", []):
        price_type = str(item.get("Type", "")).lower()
        price_raw = item.get("Price")
        status_raw = item.get("Status", "")
        start_raw = item.get("StartDateTime", "")

        # Parse start time for display label
        display_time = ""
        if start_raw:
            try:
                dt = datetime.fromisoformat(start_raw)
                display_time = dt.astimezone().strftime("%I:%M %p").lstrip("0")
            except (ValueError, TypeError):
                display_time = ""

        price_val = float(price_raw) if price_raw is not None else None

        if price_type == "current" and not current:
            current = {
                "price": price_val,
                "status": _normalise_status(status_raw),
                "status_icon": _STATUS_ICONS.get(_normalise_status(status_raw), " "),
                "time": display_time,
            }
        elif price_type == "forecast" and len(forecast) < 24:
            # Skip forecast entries that are in the past
            try:
                dt = datetime.fromisoformat(start_raw)
                if dt < time_now:
                    continue
            except (ValueError, TypeError):
                pass
            forecast.append({
                "price": price_val,
                "status": _normalise_status(status_raw),
                "status_icon": _STATUS_ICONS.get(_normalise_status(status_raw), " "),
                "time": display_time,
            })

    return current, forecast


class TopicPowerController:
    """Polls the PowerController DataAPI and exposes outputs, meters, probes, UPS status, and energy prices."""

    def __init__(
        self,
        base_url: str,
        on_update: Callable[[], None],
        logger: SCLogger,
        access_key: str | None = None,
        refresh_interval_sec: int = 10,
        ups_name: str | None = None,
        temp_probe_max_age_hours: float = 2,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._access_key = access_key
        self._on_update = on_update
        self._logger = logger
        self._refresh_secs = max(5, int(refresh_interval_sec))
        self._ups_name = ups_name or None
        self._temp_probe_max_age_hours = temp_probe_max_age_hours
        self._ups_name_warned = False
        self._ups_endpoint_warned = False

        self._lock = threading.Lock()
        self._pc_data: dict = {"connected": False, "outputs": [], "meters": [], "probes": [], "ups": [], "ups_selected": None}
        self._amber_data: dict = {"connected": False, "current_price": None, "current_status": "medium", "current_status_icon": "🟡", "forecast": []}

    def get_data(self) -> dict:
        with self._lock:
            return {
                "powercontroller": dict(self._pc_data),
                "amber_energy": dict(self._amber_data),
            }

    def run(self, stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            try:
                self._fetch_all()
                self._on_update()
            except requests.exceptions.ConnectionError:
                self._logger.log_message(
                    f"PowerController DataAPI not reachable at {self._base_url}", "warning"
                )
                with self._lock:
                    self._pc_data["connected"] = False
                    self._amber_data["connected"] = False
            except Exception as e:  # noqa: BLE001
                self._logger.log_message(f"PowerController fetch error: {e}", "warning")
                with self._lock:
                    self._pc_data["connected"] = False
                    self._amber_data["connected"] = False

            stop_event.wait(timeout=self._refresh_secs)

    # ── Private ──────────────────────────────────────────────────────────

    def _fetch_all(self) -> None:
        outputs_raw = self._get("/outputs")
        meters_raw = self._get("/meters")
        probes_raw = self._get("/tempprobes")
        prices_raw = self._get("/energyprices")
        ups_raw = self._get_ups()

        outputs = _parse_outputs(outputs_raw if isinstance(outputs_raw, dict) else {})
        meters = _parse_meters(meters_raw if isinstance(meters_raw, dict) else {})
        probes = _parse_probes(probes_raw if isinstance(probes_raw, dict) else {}, self._temp_probe_max_age_hours)
        ups = _parse_ups(ups_raw if isinstance(ups_raw, dict) else {})
        ups_selected = _select_ups(ups, self._ups_name)
        if self._ups_name and ups and ups_selected is None and not self._ups_name_warned:
            self._logger.log_message(
                f"PowerController UPS '{self._ups_name}' not found in /ups data "
                f"(available: {', '.join(u['name'] for u in ups)})", "warning"
            )
            self._ups_name_warned = True
        current_price, forecast = _parse_energy_prices(prices_raw if isinstance(prices_raw, dict) else {})

        with self._lock:
            self._pc_data = {
                "connected": True,
                "outputs": outputs,
                "meters": meters,
                "probes": probes,
                "ups": ups,
                "ups_selected": ups_selected,
            }
            self._amber_data = {
                "connected": True,
                "current_price": current_price.get("price"),
                "current_status": current_price.get("status", "medium"),
                "current_status_icon": current_price.get("status_icon", " "),
                "forecast": forecast,
            }

    def _get_ups(self) -> Any:
        """Fetch the /ups data, which older PowerController versions don't provide.

        Returns:
            The JSON response from the /ups endpoint, or an empty dict if it isn't available.
        """
        try:
            return self._get("/ups")
        except requests.exceptions.HTTPError as e:
            if not self._ups_endpoint_warned:
                self._logger.log_message(f"PowerController /ups data not available: {e}", "warning")
                self._ups_endpoint_warned = True
            return {}

    def _get(self, endpoint: str) -> Any:
        url = self._base_url + endpoint
        headers: dict[str, str] = {}
        if self._access_key:
            headers["Authorization"] = f"Bearer {self._access_key}"
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        return resp.json()
