"""Shared entity plumbing for the Sensorist integration."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import (
    SensoristDataUpdateCoordinator,
    SensoristGateway,
    SensoristSensor,
)


def gateway_device_identifier(gateway_id: int) -> tuple[str, str]:
    """Return the device registry identifier for a gateway.

    Gateway and sensor ids come from separate sequences and could collide, so
    gateway identifiers carry a prefix.
    """
    return (DOMAIN, f"gateway_{gateway_id}")


def gateway_device_info(gateway: SensoristGateway) -> DeviceInfo:
    """Build device info for a Sensorist gateway."""
    return DeviceInfo(
        identifiers={gateway_device_identifier(gateway.id)},
        manufacturer=MANUFACTURER,
        model=gateway.model,
        name=gateway.title,
        serial_number=gateway.serial,
        sw_version=gateway.firmware,
    )


def sensor_device_info(sensor: SensoristSensor) -> DeviceInfo:
    """Build device info for a Sensorist sensor, linked to its gateway."""
    return DeviceInfo(
        identifiers={(DOMAIN, str(sensor.id))},
        manufacturer=MANUFACTURER,
        model=sensor.model,
        name=sensor.title,
        serial_number=sensor.serial,
        sw_version=sensor.firmware,
        via_device=gateway_device_identifier(sensor.gateway.id),
    )


class SensoristEntity(CoordinatorEntity[SensoristDataUpdateCoordinator]):
    """Base entity carrying the shared naming convention."""

    _attr_has_entity_name = True
