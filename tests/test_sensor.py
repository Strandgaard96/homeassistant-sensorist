"""Tests for the Sensorist sensor entities."""

from __future__ import annotations

import copy
from datetime import timedelta

from aioresponses import aioresponses
from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.sensor import (
    ATTR_STATE_CLASS,
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.const import (
    ATTR_DEVICE_CLASS,
    ATTR_UNIT_OF_MEASUREMENT,
    PERCENTAGE,
    STATE_UNAVAILABLE,
    EntityCategory,
    UnitOfElectricPotential,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sensorist.const import DOMAIN

from .conftest import (
    BATTERY_DS,
    CAPTURED_AT,
    GATEWAY_ID,
    HUMIDITY_DS,
    SENSOR_TWO_ID,
    TEMPERATURE_DS,
    URL_MEASUREMENTS,
    WIRELESS_DS,
    get_state,
    load_fixture_body,
    mock_full_account,
    setup_integration,
)


def entity_id_for(hass: HomeAssistant, unique_id: str) -> str:
    """Look an entity up by unique id rather than guessing its slug."""
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("sensor", DOMAIN, unique_id)
    assert entity_id is not None
    return entity_id


async def test_all_data_sources_become_entities(
    hass: HomeAssistant, mock_api: aioresponses, config_entry: MockConfigEntry
) -> None:
    """Eight data sources produce eight sensor entities."""
    mock_full_account(mock_api)
    await setup_integration(hass, config_entry)

    registry = er.async_get(hass)
    entities = er.async_entries_for_config_entry(registry, config_entry.entry_id)
    assert len([e for e in entities if e.domain == "sensor"]) == 8


async def test_temperature_entity(
    hass: HomeAssistant,
    mock_api: aioresponses,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Temperature carries the right device class, unit and value."""
    freezer.move_to(CAPTURED_AT)
    mock_full_account(mock_api)
    await setup_integration(hass, config_entry)

    state = hass.states.get(entity_id_for(hass, str(TEMPERATURE_DS)))
    assert state is not None
    assert state.state == "20.34"
    assert state.attributes[ATTR_DEVICE_CLASS] == SensorDeviceClass.TEMPERATURE
    assert state.attributes[ATTR_STATE_CLASS] == SensorStateClass.MEASUREMENT
    assert state.attributes[ATTR_UNIT_OF_MEASUREMENT] == UnitOfTemperature.CELSIUS
    assert state.attributes["last_measured"] == "2026-09-13T16:59:04+00:00"


async def test_humidity_entity(
    hass: HomeAssistant,
    mock_api: aioresponses,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Humidity is a percentage measurement."""
    freezer.move_to(CAPTURED_AT)
    mock_full_account(mock_api)
    await setup_integration(hass, config_entry)

    state = hass.states.get(entity_id_for(hass, str(HUMIDITY_DS)))
    assert state is not None
    assert state.state == "70.8"
    assert state.attributes[ATTR_DEVICE_CLASS] == SensorDeviceClass.HUMIDITY
    assert state.attributes[ATTR_UNIT_OF_MEASUREMENT] == PERCENTAGE


async def test_battery_is_voltage_not_percentage(
    hass: HomeAssistant,
    mock_api: aioresponses,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """The API reports battery in volts, so the entity must not claim percent."""
    freezer.move_to(CAPTURED_AT)
    mock_full_account(mock_api)
    await setup_integration(hass, config_entry)

    entity_id = entity_id_for(hass, str(BATTERY_DS))
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "2.83"
    assert state.attributes[ATTR_DEVICE_CLASS] == SensorDeviceClass.VOLTAGE
    assert state.attributes[ATTR_UNIT_OF_MEASUREMENT] == UnitOfElectricPotential.VOLT

    entry = er.async_get(hass).async_get(entity_id)
    assert entry is not None
    assert entry.entity_category is EntityCategory.DIAGNOSTIC


async def test_wireless_has_no_device_class(
    hass: HomeAssistant,
    mock_api: aioresponses,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Wireless quality is a percentage, which no HA device class accepts."""
    freezer.move_to(CAPTURED_AT)
    mock_full_account(mock_api)
    await setup_integration(hass, config_entry)

    entity_id = entity_id_for(hass, str(WIRELESS_DS))
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "100.0"
    assert ATTR_DEVICE_CLASS not in state.attributes
    assert state.attributes[ATTR_UNIT_OF_MEASUREMENT] == PERCENTAGE

    entry = er.async_get(hass).async_get(entity_id)
    assert entry is not None
    assert entry.entity_category is EntityCategory.DIAGNOSTIC


async def test_unknown_kind_still_produces_an_entity(
    hass: HomeAssistant,
    mock_api: aioresponses,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """An unmapped data source kind must not break setup."""
    freezer.move_to(CAPTURED_AT)
    gateways = copy.deepcopy(load_fixture_body("gateways_with_slaves"))
    data_source = gateways["gateways"][0]["devices"][0]["devices"][0]
    data_source["title"] = "Air pressure"
    data_source["type"] = {
        "capability": 99,
        "id": 42,
        "name": "press",
        "precision": 0,
        "unit": {"id": 9, "name": "hPa"},
    }
    mock_full_account(mock_api, gateways=gateways)
    await setup_integration(hass, config_entry)

    entity_id = entity_id_for(hass, str(data_source["id"]))
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state != STATE_UNAVAILABLE
    # No device class means Home Assistant accepts whatever unit the API gave.
    assert ATTR_DEVICE_CLASS not in state.attributes
    assert state.attributes[ATTR_UNIT_OF_MEASUREMENT] == "hPa"
    assert state.attributes["friendly_name"].endswith("Air pressure")


async def test_stale_measurement_uses_its_own_interval(
    hass: HomeAssistant,
    mock_api: aioresponses,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Staleness follows each data source's reporting rate, not one global timeout.

    Fifty minutes on, temperature (900 s interval, 45 min threshold) has gone
    stale while battery (10800 s interval, 9 h threshold) is still fresh. The
    gap stays under an hour so the inventory is not refetched as a side effect.
    """
    freezer.move_to(CAPTURED_AT)
    mock_full_account(mock_api, repeat=False)
    await setup_integration(hass, config_entry)

    temperature_id = entity_id_for(hass, str(TEMPERATURE_DS))
    battery_id = entity_id_for(hass, str(BATTERY_DS))
    assert get_state(hass, temperature_id).state == "20.34"

    # Same payload again: the readings have not been refreshed by the hardware.
    mock_api.get(
        URL_MEASUREMENTS,
        status=200,
        payload=load_fixture_body("measurements_latest"),
        repeat=True,
    )
    freezer.tick(timedelta(minutes=50))
    await config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    assert get_state(hass, temperature_id).state == STATE_UNAVAILABLE
    assert get_state(hass, battery_id).state == "2.83"


async def test_devices_are_linked_via_the_gateway(
    hass: HomeAssistant, mock_api: aioresponses, config_entry: MockConfigEntry
) -> None:
    """Each sensor is its own device, hanging off the gateway device."""
    mock_full_account(mock_api)
    await setup_integration(hass, config_entry)

    registry = dr.async_get(hass)
    gateway = registry.async_get_device(identifiers={(DOMAIN, f"gateway_{GATEWAY_ID}")})
    assert gateway is not None
    assert gateway.serial_number == "10000000"
    assert gateway.model == "B16"

    sensor = registry.async_get_device(identifiers={(DOMAIN, str(SENSOR_TWO_ID))})
    assert sensor is not None
    assert sensor.manufacturer == "Sensorist"
    assert sensor.model == "S2.0"
    assert sensor.serial_number == "aa00bb01"
    assert sensor.via_device_id == gateway.id
