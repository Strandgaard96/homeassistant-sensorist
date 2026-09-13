"""Sensor platform for the Sensorist integration."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfElectricPotential,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import (
    KIND_BATTERY,
    KIND_HUMIDITY,
    KIND_TEMPERATURE,
    KIND_WIRELESS,
    STALE_INTERVAL_FACTOR,
)
from .coordinator import (
    SensoristConfigEntry,
    SensoristDataSource,
    SensoristDataUpdateCoordinator,
    SensoristMeasurement,
)
from .entity import SensoristEntity, sensor_device_info

# The coordinator fetches everything in one request set; entities never poll.
PARALLEL_UPDATES = 0

# Keyed on the data source's numeric type.id, which is stable, rather than on
# type.name.
SENSOR_DESCRIPTIONS: dict[int, SensorEntityDescription] = {
    KIND_TEMPERATURE: SensorEntityDescription(
        key="temperature",
        translation_key="temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        suggested_display_precision=1,
    ),
    KIND_HUMIDITY: SensorEntityDescription(
        key="humidity",
        translation_key="humidity",
        device_class=SensorDeviceClass.HUMIDITY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        suggested_display_precision=0,
    ),
    # The API reports battery in volts, not percent, so the BATTERY device class
    # (which HA restricts to percentages) does not apply. Converting volts to a
    # percentage would require cell chemistry and cut-off thresholds the API
    # does not expose, so the raw voltage is reported instead.
    KIND_BATTERY: SensorEntityDescription(
        key="battery_voltage",
        translation_key="battery_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        suggested_display_precision=2,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    # Wireless quality is a percentage. HA's SIGNAL_STRENGTH device class only
    # permits dB/dBm, so this stays a plain measurement.
    KIND_WIRELESS: SensorEntityDescription(
        key="wireless",
        translation_key="wireless",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        suggested_display_precision=0,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
}


# The API reports the unit for every data source, and an account may be set to
# a different one than the account these descriptions were derived from (users
# carries a temp_unit preference). Unit, device class and precision are
# therefore resolved from the API rather than baked in.
UNIT_BY_API_NAME: dict[str, str] = {
    "°c": UnitOfTemperature.CELSIUS,
    "°f": UnitOfTemperature.FAHRENHEIT,
    "c": UnitOfTemperature.CELSIUS,
    "f": UnitOfTemperature.FAHRENHEIT,
    "%": PERCENTAGE,
    "v": UnitOfElectricPotential.VOLT,
    "mv": UnitOfElectricPotential.MILLIVOLT,
}

TEMPERATURE_UNITS = frozenset({UnitOfTemperature.CELSIUS, UnitOfTemperature.FAHRENHEIT})
VOLTAGE_UNITS = frozenset({UnitOfElectricPotential.VOLT, UnitOfElectricPotential.MILLIVOLT})


def _device_class_for(kind_id: int | None, unit: str) -> SensorDeviceClass | None:
    """Return the device class that matches the unit the API actually reports."""
    if unit in TEMPERATURE_UNITS:
        return SensorDeviceClass.TEMPERATURE
    if unit in VOLTAGE_UNITS:
        return SensorDeviceClass.VOLTAGE
    if unit == PERCENTAGE:
        if kind_id == KIND_BATTERY:
            # A sensor model that reports a real percentage can use the battery
            # device class, unlike the volt-reporting hardware seen so far.
            return SensorDeviceClass.BATTERY
        if kind_id == KIND_HUMIDITY:
            return SensorDeviceClass.HUMIDITY
    return None


def _fallback_description(data_source: SensoristDataSource) -> SensorEntityDescription:
    """Describe a data source kind that discovery never saw.

    Unit ids observed in the wild are not contiguous, so unmapped kinds exist.
    Without a device class Home Assistant accepts whatever unit string the API
    supplies, which keeps an unknown kind working instead of crashing.
    """
    return SensorEntityDescription(
        key=f"kind_{data_source.kind_id}",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=data_source.unit,
        suggested_display_precision=data_source.precision,
    )


def describe(data_source: SensoristDataSource) -> SensorEntityDescription:
    """Return the entity description for a data source.

    The mapped description is a template: whatever unit and precision the API
    reports win, so an account configured in Fahrenheit, or hardware reporting
    battery as a percentage, is described correctly rather than relabelled.
    """
    description = SENSOR_DESCRIPTIONS.get(data_source.kind_id or -1)
    if description is None:
        return _fallback_description(data_source)

    unit = description.native_unit_of_measurement
    device_class = description.device_class
    precision = description.suggested_display_precision

    if data_source.precision is not None:
        precision = data_source.precision

    if data_source.unit:
        mapped = UNIT_BY_API_NAME.get(data_source.unit.lower())
        if mapped is None:
            # An unrecognised unit cannot be validated against a device class,
            # so pass the API's string through undescribed.
            unit = data_source.unit
            device_class = None
        elif mapped != unit:
            unit = mapped
            device_class = _device_class_for(data_source.kind_id, mapped)

    if (
        unit == description.native_unit_of_measurement
        and device_class == description.device_class
        and precision == description.suggested_display_precision
    ):
        return description

    return replace(
        description,
        native_unit_of_measurement=unit,
        device_class=device_class,
        suggested_display_precision=precision,
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SensoristConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Sensorist sensors from a config entry."""
    coordinator = entry.runtime_data
    async_add_entities(
        SensoristSensorEntity(coordinator, data_source)
        for data_source in coordinator.data.data_sources.values()
    )


class SensoristSensorEntity(SensoristEntity, SensorEntity):
    """A single Sensorist data source."""

    def __init__(
        self,
        coordinator: SensoristDataUpdateCoordinator,
        data_source: SensoristDataSource,
    ) -> None:
        """Initialise the entity for one data source."""
        super().__init__(coordinator)
        self._data_source_id = data_source.id
        description = describe(data_source)
        if description.translation_key is None:
            # No translation exists for an unknown kind, so fall back to the
            # label the API gave the data source.
            self._attr_name = data_source.title
        self.entity_description = description
        self._attr_unique_id = str(data_source.id)
        self._attr_device_info = sensor_device_info(data_source.sensor)

    @property
    def _data_source(self) -> SensoristDataSource | None:
        """Return the current inventory entry for this data source."""
        return self.coordinator.data.data_sources.get(self._data_source_id)

    @property
    def _measurement(self) -> SensoristMeasurement | None:
        """Return the latest measurement for this data source."""
        return self.coordinator.data.measurements.get(self._data_source_id)

    @property
    def _stale_after(self) -> timedelta:
        """Return how old a measurement may get before the entity goes away.

        Data sources report at very different rates -- 900 s for temperature,
        10800 s for battery -- so the threshold follows the data source's own
        interval rather than the coordinator's.
        """
        coordinator_seconds = (
            self.coordinator.update_interval.total_seconds()
            if self.coordinator.update_interval
            else 0
        )
        data_source = self._data_source
        interval = max(coordinator_seconds, (data_source.interval or 0) if data_source else 0)
        return timedelta(seconds=interval * STALE_INTERVAL_FACTOR)

    @property
    def available(self) -> bool:
        """Return whether the reading is recent enough to trust."""
        if not super().available:
            return False
        measurement = self._measurement
        if measurement is None:
            return False
        if measurement.timestamp is None:
            # No timestamp means staleness cannot be judged; trust the value.
            return True
        return dt_util.utcnow() - measurement.timestamp < self._stale_after

    @property
    def native_value(self) -> float | None:
        """Return the latest measured value."""
        measurement = self._measurement
        return measurement.value if measurement else None

    @property
    def extra_state_attributes(self) -> dict[str, str] | None:
        """Expose when the value was measured, which can lag the poll."""
        measurement = self._measurement
        if measurement is None or measurement.timestamp is None:
            return None
        return {"last_measured": measurement.timestamp.isoformat()}
