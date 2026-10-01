"""Tests that the integration fits accounts other than the one it was built from.

Nothing about the hierarchy is hardcoded, and the unit, precision and device
class of every entity come from the API rather than from the sample account.
"""

from __future__ import annotations

import copy

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import (
    ATTR_DEVICE_CLASS,
    ATTR_UNIT_OF_MEASUREMENT,
    PERCENTAGE,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.sensorist.const import DOMAIN

from .conftest import (
    BATTERY_DS,
    CAPTURED_AT,
    TEMPERATURE_DS,
    URL_MEASUREMENTS,
    get_state,
    load_fixture_body,
    mock_full_account,
    request_count,
    setup_integration,
)


def entity_id_for(hass: HomeAssistant, data_source_id: int) -> str:
    """Look a sensor up by data source id."""
    entity_id = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, str(data_source_id))
    assert entity_id is not None
    return entity_id


async def test_fahrenheit_account(
    hass: HomeAssistant,
    mock_api: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """An account configured in Fahrenheit is described in Fahrenheit."""
    freezer.move_to(CAPTURED_AT)
    gateways = copy.deepcopy(load_fixture_body("gateways_with_slaves"))
    for sensor in gateways["gateways"][0]["devices"]:
        for data_source in sensor["devices"]:
            if data_source["type"]["name"] == "temp":
                data_source["type"]["unit"] = {"id": 4, "name": "°F"}
    mock_full_account(mock_api, gateways=gateways)
    await setup_integration(hass, config_entry)

    state = get_state(hass, entity_id_for(hass, TEMPERATURE_DS))
    assert state.attributes[ATTR_DEVICE_CLASS] == SensorDeviceClass.TEMPERATURE
    # Home Assistant converts to the system unit for display, which is the proof
    # that the entity declared Fahrenheit natively: 20.34 F is about -6.5 C.
    assert state.attributes[ATTR_UNIT_OF_MEASUREMENT] == UnitOfTemperature.CELSIUS
    assert float(state.state) == pytest.approx(-6.48, abs=0.05)


async def test_battery_reported_as_percentage(
    hass: HomeAssistant,
    mock_api: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Hardware that reports a real battery percentage gets the battery class."""
    freezer.move_to(CAPTURED_AT)
    gateways = copy.deepcopy(load_fixture_body("gateways_with_slaves"))
    for sensor in gateways["gateways"][0]["devices"]:
        for data_source in sensor["devices"]:
            if data_source["type"]["name"] == "batt":
                data_source["type"]["unit"] = {"id": 2, "name": "%"}
                data_source["type"]["precision"] = 0
    measurements = copy.deepcopy(load_fixture_body("measurements_latest"))
    measurements["measurements"][str(BATTERY_DS)]["value"] = 87
    mock_full_account(mock_api, gateways=gateways, measurements=measurements)
    await setup_integration(hass, config_entry)

    state = get_state(hass, entity_id_for(hass, BATTERY_DS))
    assert state.state == "87.0"
    assert state.attributes[ATTR_DEVICE_CLASS] == SensorDeviceClass.BATTERY
    assert state.attributes[ATTR_UNIT_OF_MEASUREMENT] == PERCENTAGE


async def test_unrecognised_unit_on_a_known_kind(
    hass: HomeAssistant, mock_api: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """A unit we cannot validate is passed through without a device class."""
    gateways = copy.deepcopy(load_fixture_body("gateways_with_slaves"))
    for sensor in gateways["gateways"][0]["devices"]:
        for data_source in sensor["devices"]:
            if data_source["type"]["name"] == "temp":
                data_source["type"]["unit"] = {"id": 77, "name": "K"}
    mock_full_account(mock_api, gateways=gateways)
    await setup_integration(hass, config_entry)

    state = get_state(hass, entity_id_for(hass, TEMPERATURE_DS))
    assert ATTR_DEVICE_CLASS not in state.attributes
    assert state.attributes[ATTR_UNIT_OF_MEASUREMENT] == "K"


async def test_precision_comes_from_the_api(
    hass: HomeAssistant, mock_api: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """A data source that declares more precision is displayed with it."""
    gateways = copy.deepcopy(load_fixture_body("gateways_with_slaves"))
    for sensor in gateways["gateways"][0]["devices"]:
        for data_source in sensor["devices"]:
            if data_source["type"]["name"] == "temp":
                data_source["type"]["precision"] = 3
    mock_full_account(mock_api, gateways=gateways)
    await setup_integration(hass, config_entry)

    entry = er.async_get(hass).async_get(entity_id_for(hass, TEMPERATURE_DS))
    assert entry is not None
    assert entry.options["sensor"]["suggested_display_precision"] == 3


async def test_multiple_gateways_are_fetched_separately(
    hass: HomeAssistant, mock_api: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """The API rejects mixed-gateway requests, so each gateway gets its own."""
    gateways = copy.deepcopy(load_fixture_body("gateways_with_slaves"))
    second = copy.deepcopy(gateways["gateways"][0])
    second["id"] = 7326
    second["serial"] = "10117779"
    second["title"] = "Shed gateway"
    second["master_id"] = 7325
    offset = 1000
    for sensor in second["devices"]:
        sensor["id"] += offset
        sensor["current_gateway_id"] = 7326
        sensor["serial"] = f"s{sensor['id']}"
        for data_source in sensor["devices"]:
            data_source["id"] += offset
    gateways["gateways"].append(second)

    measurements = copy.deepcopy(load_fixture_body("measurements_latest"))
    for source_id, measurement in list(measurements["measurements"].items()):
        measurements["measurements"][str(int(source_id) + offset)] = measurement

    mock_full_account(mock_api, gateways=gateways, measurements=measurements)
    await setup_integration(hass, config_entry)

    # Two gateways, two sensors each, four data sources per sensor.
    assert request_count(mock_api, URL_MEASUREMENTS) == 2

    registry = er.async_get(hass)
    entities = er.async_entries_for_config_entry(registry, config_entry.entry_id)
    assert len([e for e in entities if e.domain == "sensor"]) == 16
    assert len([e for e in entities if e.domain == "binary_sensor"]) == 2

    devices = dr.async_get(hass)
    assert (
        devices.async_get_device_by_identifier((DOMAIN, "gateway_7326"), config_entry.entry_id)
        is not None
    )


async def test_account_with_no_hardware(
    hass: HomeAssistant, mock_api: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """An empty account sets up cleanly with no entities and no measurement call."""
    mock_full_account(mock_api, gateways={"code": 200, "max_age": 900, "gateways": []})
    await setup_integration(hass, config_entry)

    registry = er.async_get(hass)
    assert er.async_entries_for_config_entry(registry, config_entry.entry_id) == []
    assert request_count(mock_api, URL_MEASUREMENTS) == 0
