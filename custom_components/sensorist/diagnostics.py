"""Diagnostics support for the Sensorist integration."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant

from .coordinator import SensoristConfigEntry

TO_REDACT = {CONF_PASSWORD, CONF_EMAIL}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: SensoristConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    data = coordinator.data

    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "unique_id": entry.unique_id,
        },
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "update_interval": (
                coordinator.update_interval.total_seconds() if coordinator.update_interval else None
            ),
        },
        # Gateway objects never carry ipv4/local_ipv4 past the coordinator, so
        # no network addresses can reach diagnostics.
        "gateways": [asdict(gateway) for gateway in data.gateways.values()],
        "data_sources": [asdict(source) for source in data.data_sources.values()],
        "measurements": {
            str(source_id): asdict(measurement)
            for source_id, measurement in data.measurements.items()
        },
    }
