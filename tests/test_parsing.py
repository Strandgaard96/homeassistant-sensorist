"""Tests for defensive parsing of malformed or unexpected API payloads.

The API's field names are undocumented, so anything unexpected must degrade to
a missing entity rather than an exception during setup.
"""

from __future__ import annotations

import copy

from aioresponses import aioresponses
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sensorist.const import DOMAIN
from custom_components.sensorist.coordinator import SensoristDataUpdateCoordinator

from .conftest import (
    CAPTURED_AT,
    TEMPERATURE_DS,
    get_state,
    load_fixture_body,
    mock_full_account,
    setup_integration,
)


async def test_gateway_without_id_is_skipped(
    hass: HomeAssistant, mock_api: aioresponses, config_entry: MockConfigEntry
) -> None:
    """A gateway with no id cannot be addressed, so it is dropped."""
    gateways = copy.deepcopy(load_fixture_body("gateways_with_slaves"))
    del gateways["gateways"][0]["id"]
    mock_full_account(mock_api, gateways=gateways)
    await setup_integration(hass, config_entry)

    coordinator: SensoristDataUpdateCoordinator = config_entry.runtime_data
    assert coordinator.data.gateways == {}
    assert coordinator.data.data_sources == {}


async def test_data_source_without_id_is_skipped(
    hass: HomeAssistant, mock_api: aioresponses, config_entry: MockConfigEntry
) -> None:
    """One unusable data source must not cost the other seven."""
    gateways = copy.deepcopy(load_fixture_body("gateways_with_slaves"))
    del gateways["gateways"][0]["devices"][0]["devices"][0]["id"]
    mock_full_account(mock_api, gateways=gateways)
    await setup_integration(hass, config_entry)

    coordinator: SensoristDataUpdateCoordinator = config_entry.runtime_data
    assert len(coordinator.data.data_sources) == 7


async def test_missing_type_object_still_yields_an_entity(
    hass: HomeAssistant, mock_api: aioresponses, config_entry: MockConfigEntry
) -> None:
    """A data source with no type block falls back to an undescribed sensor."""
    gateways = copy.deepcopy(load_fixture_body("gateways_with_slaves"))
    data_source = gateways["gateways"][0]["devices"][0]["devices"][0]
    del data_source["type"]
    mock_full_account(mock_api, gateways=gateways)
    await setup_integration(hass, config_entry)

    coordinator: SensoristDataUpdateCoordinator = config_entry.runtime_data
    parsed = coordinator.data.data_sources[data_source["id"]]
    assert parsed.kind_id is None
    assert parsed.unit is None

    entity_id = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, str(data_source["id"]))
    assert entity_id is not None


async def test_non_numeric_measurement_is_ignored(
    hass: HomeAssistant, mock_api: aioresponses, config_entry: MockConfigEntry
) -> None:
    """A value that is not a number is not a reading."""
    measurements = copy.deepcopy(load_fixture_body("measurements_latest"))
    measurements["measurements"][str(TEMPERATURE_DS)]["value"] = "warm"
    mock_full_account(mock_api, measurements=measurements)
    await setup_integration(hass, config_entry)

    coordinator: SensoristDataUpdateCoordinator = config_entry.runtime_data
    assert TEMPERATURE_DS not in coordinator.data.measurements


async def test_data_source_without_measurement_is_unavailable(
    hass: HomeAssistant, mock_api: aioresponses, config_entry: MockConfigEntry
) -> None:
    """A data source the API returned no reading for has nothing to show."""
    measurements = copy.deepcopy(load_fixture_body("measurements_latest"))
    del measurements["measurements"][str(TEMPERATURE_DS)]
    mock_full_account(mock_api, measurements=measurements)
    await setup_integration(hass, config_entry)

    entity_id = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, str(TEMPERATURE_DS))
    assert entity_id is not None
    state = get_state(hass, entity_id)
    assert state.state == STATE_UNAVAILABLE
    assert "last_measured" not in state.attributes


async def test_measurement_without_timestamp_is_trusted(
    hass: HomeAssistant,
    mock_api: aioresponses,
    config_entry: MockConfigEntry,
) -> None:
    """With no date, staleness cannot be judged, so the value still shows."""
    measurements = copy.deepcopy(load_fixture_body("measurements_latest"))
    del measurements["measurements"][str(TEMPERATURE_DS)]["date"]
    mock_full_account(mock_api, measurements=measurements)
    await setup_integration(hass, config_entry)

    entity_id = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, str(TEMPERATURE_DS))
    assert entity_id is not None
    state = get_state(hass, entity_id)
    assert state.state == "20.34"
    assert "last_measured" not in state.attributes


async def test_measurements_for_unknown_ids_are_ignored(
    hass: HomeAssistant, mock_api: aioresponses, config_entry: MockConfigEntry
) -> None:
    """A reading for something not in the inventory cannot create an entity."""
    measurements = copy.deepcopy(load_fixture_body("measurements_latest"))
    measurements["measurements"]["not-a-number"] = {
        "date": CAPTURED_AT,
        "type": 1,
        "value": 1,
    }
    mock_full_account(mock_api, measurements=measurements)
    await setup_integration(hass, config_entry)

    registry = er.async_get(hass)
    entities = er.async_entries_for_config_entry(registry, config_entry.entry_id)
    assert len([e for e in entities if e.domain == "sensor"]) == 8
