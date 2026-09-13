"""The Sensorist integration."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .coordinator import SensoristConfigEntry, SensoristDataUpdateCoordinator
from .entity import gateway_device_info

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: SensoristConfigEntry) -> bool:
    """Set up Sensorist from a config entry."""
    coordinator = SensoristDataUpdateCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator

    # Register the gateways before the platforms load. A sensor's via_device
    # link is resolved when its device is created, so the gateway it points at
    # has to exist first -- and platform setup order is not guaranteed.
    device_registry = dr.async_get(hass)
    for gateway in coordinator.data.gateways.values():
        device_registry.async_get_or_create(
            config_entry_id=entry.entry_id, **gateway_device_info(gateway)
        )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SensoristConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
