"""Binary sensor platform for the Sensorist integration.

The API exposes no last-seen timestamp for a gateway. The only connectivity
signal is disconnected_date, which is null while the gateway is online, so a
connectivity binary sensor is the honest representation -- a timestamp sensor
would read "unknown" during normal operation.
"""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import (
    SensoristConfigEntry,
    SensoristDataUpdateCoordinator,
    SensoristGateway,
)
from .entity import SensoristEntity, gateway_device_info

PARALLEL_UPDATES = 0

CONNECTIVITY_DESCRIPTION = BinarySensorEntityDescription(
    key="connectivity",
    translation_key="connectivity",
    device_class=BinarySensorDeviceClass.CONNECTIVITY,
    entity_category=EntityCategory.DIAGNOSTIC,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SensoristConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the gateway connectivity sensors."""
    coordinator = entry.runtime_data
    async_add_entities(
        SensoristGatewayConnectivity(coordinator, gateway)
        for gateway in coordinator.data.gateways.values()
    )


class SensoristGatewayConnectivity(SensoristEntity, BinarySensorEntity):
    """Reports whether a gateway is currently connected to the cloud."""

    entity_description = CONNECTIVITY_DESCRIPTION

    def __init__(
        self,
        coordinator: SensoristDataUpdateCoordinator,
        gateway: SensoristGateway,
    ) -> None:
        """Initialise the entity for one gateway."""
        super().__init__(coordinator)
        self._gateway_id = gateway.id
        self._attr_unique_id = f"gateway_{gateway.id}_connectivity"
        self._attr_device_info = gateway_device_info(gateway)

    @property
    def _gateway(self) -> SensoristGateway | None:
        """Return the current inventory entry for this gateway."""
        return self.coordinator.data.gateways.get(self._gateway_id)

    @property
    def available(self) -> bool:
        """Return whether the gateway is still present in the inventory."""
        return super().available and self._gateway is not None

    @property
    def is_on(self) -> bool | None:
        """Return True while the gateway is connected."""
        gateway = self._gateway
        return gateway.is_connected if gateway else None

    @property
    def extra_state_attributes(self) -> dict[str, str] | None:
        """Expose when the gateway dropped off, if it is currently offline."""
        gateway = self._gateway
        if gateway is None or gateway.disconnected_date is None:
            return None
        return {"disconnected_since": gateway.disconnected_date.isoformat()}
