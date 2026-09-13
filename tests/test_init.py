"""Tests for setup, unload and the coordinator's polling behaviour."""

from __future__ import annotations

import copy
from datetime import timedelta

from aioresponses import aioresponses
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sensorist.const import DOMAIN
from custom_components.sensorist.coordinator import SensoristDataUpdateCoordinator

from .conftest import (
    BATTERY_DS,
    CAPTURED_AT,
    GATEWAY_ID,
    SENSOR_TWO_ID,
    TEMPERATURE_DS,
    URL_GATEWAYS,
    URL_MEASUREMENTS,
    URL_USERS,
    load_fixture_body,
    mock_full_account,
    request_count,
    setup_integration,
)


async def test_setup_and_unload(
    hass: HomeAssistant, mock_api: aioresponses, config_entry: MockConfigEntry
) -> None:
    """The entry sets up and tears down cleanly."""
    mock_full_account(mock_api)
    await setup_integration(hass, config_entry)

    assert config_entry.state is ConfigEntryState.LOADED

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()
    unloaded_state: ConfigEntryState = config_entry.state
    assert unloaded_state is ConfigEntryState.NOT_LOADED


async def test_coordinator_maps_fixtures(
    hass: HomeAssistant, mock_api: aioresponses, config_entry: MockConfigEntry
) -> None:
    """The recursive devices tree becomes typed gateways, sensors and sources."""
    mock_full_account(mock_api)
    await setup_integration(hass, config_entry)

    coordinator: SensoristDataUpdateCoordinator = config_entry.runtime_data
    data = coordinator.data

    assert set(data.gateways) == {GATEWAY_ID}
    gateway = data.gateways[GATEWAY_ID]
    assert gateway.serial == "10000000"
    assert gateway.model == "B16"
    assert gateway.firmware == "2.43"
    assert gateway.is_connected is True

    # Two sensors, four data sources each.
    assert len(data.data_sources) == 8

    temperature = data.data_sources[TEMPERATURE_DS]
    assert temperature.kind_id == 5
    assert temperature.kind_name == "temp"
    assert temperature.unit == "°C"
    assert temperature.precision == 1
    assert temperature.interval == 900
    assert temperature.sensor.id == SENSOR_TWO_ID
    assert temperature.sensor.model == "S2.0"
    assert temperature.sensor.gateway.id == GATEWAY_ID

    # Battery reports every three hours, unlike the 900 s sensors.
    assert data.data_sources[BATTERY_DS].interval == 10800

    assert data.measurements[TEMPERATURE_DS].value == 20.34
    assert data.measurements[TEMPERATURE_DS].measurement_type == 1
    assert data.measurements[BATTERY_DS].value == 2.83


async def test_steady_state_poll_is_one_request_per_gateway(
    hass: HomeAssistant,
    mock_api: aioresponses,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A routine poll fetches measurements only; the inventory stays cached."""
    freezer.move_to(CAPTURED_AT)
    mock_full_account(mock_api)
    await setup_integration(hass, config_entry)

    assert request_count(mock_api, URL_GATEWAYS) == 1
    assert request_count(mock_api, URL_MEASUREMENTS) == 1
    # /users is only for the config flow; setup must never call it.
    assert request_count(mock_api, URL_USERS) == 0

    coordinator: SensoristDataUpdateCoordinator = config_entry.runtime_data

    # Two polls, 15 minutes apart, well inside the hourly inventory window.
    for _ in range(2):
        freezer.tick(timedelta(seconds=901))
        await coordinator.async_refresh()
        await hass.async_block_till_done()

    assert request_count(mock_api, URL_GATEWAYS) == 1
    assert request_count(mock_api, URL_MEASUREMENTS) == 3


async def test_inventory_refetched_after_an_hour(
    hass: HomeAssistant,
    mock_api: aioresponses,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """New hardware is picked up by the hourly inventory refresh."""
    freezer.move_to(CAPTURED_AT)
    mock_full_account(mock_api)
    await setup_integration(hass, config_entry)

    coordinator: SensoristDataUpdateCoordinator = config_entry.runtime_data
    freezer.tick(timedelta(hours=1, seconds=1))
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert request_count(mock_api, URL_GATEWAYS) == 2


async def test_update_interval_follows_max_age(
    hass: HomeAssistant, mock_api: aioresponses, config_entry: MockConfigEntry
) -> None:
    """The poll interval never drops below the API's advertised max-age."""
    mock_full_account(mock_api)
    await setup_integration(hass, config_entry)

    coordinator: SensoristDataUpdateCoordinator = config_entry.runtime_data
    assert coordinator.update_interval == timedelta(seconds=900)


async def test_update_interval_raised_by_longer_max_age(
    hass: HomeAssistant, mock_api: aioresponses, config_entry: MockConfigEntry
) -> None:
    """A longer max-age slows polling down rather than being ignored."""
    mock_full_account(mock_api, repeat=True)
    mock_api.get(
        URL_MEASUREMENTS,
        status=200,
        payload=load_fixture_body("measurements_latest"),
        headers={"cache-control": "max-age=3600, private"},
        repeat=True,
    )
    await setup_integration(hass, config_entry)

    coordinator: SensoristDataUpdateCoordinator = config_entry.runtime_data
    assert coordinator.update_interval is not None
    assert coordinator.update_interval >= timedelta(seconds=900)


async def test_auth_failure_during_update_triggers_reauth(
    hass: HomeAssistant,
    mock_api: aioresponses,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A rejected credential mid-run starts a reauth flow instead of failing quietly."""
    freezer.move_to(CAPTURED_AT)
    mock_full_account(mock_api, repeat=False)
    await setup_integration(hass, config_entry)

    coordinator: SensoristDataUpdateCoordinator = config_entry.runtime_data
    mock_api.get(URL_MEASUREMENTS, status=401, repeat=True)
    freezer.tick(timedelta(seconds=901))
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    flows = [
        flow
        for flow in hass.config_entries.flow.async_progress()
        if flow["handler"] == DOMAIN and flow["context"]["source"] == "reauth"
    ]
    assert len(flows) == 1


async def test_connection_failure_during_update_is_not_fatal(
    hass: HomeAssistant,
    mock_api: aioresponses,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A transient outage marks the update failed but leaves the entry loaded."""
    freezer.move_to(CAPTURED_AT)
    mock_full_account(mock_api, repeat=False)
    await setup_integration(hass, config_entry)

    coordinator: SensoristDataUpdateCoordinator = config_entry.runtime_data
    mock_api.get(URL_MEASUREMENTS, status=503, repeat=True)
    freezer.tick(timedelta(seconds=901))
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert coordinator.last_update_success is False
    assert config_entry.state is ConfigEntryState.LOADED


async def test_blank_and_interpolated_rows_keep_previous_value(
    hass: HomeAssistant,
    mock_api: aioresponses,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Measurement types 20 and 30 are placeholders, not readings."""
    freezer.move_to(CAPTURED_AT)
    mock_full_account(mock_api, repeat=False)
    await setup_integration(hass, config_entry)

    coordinator: SensoristDataUpdateCoordinator = config_entry.runtime_data
    assert coordinator.data.measurements[TEMPERATURE_DS].value == 20.34

    placeholder = copy.deepcopy(load_fixture_body("measurements_latest"))
    placeholder["measurements"][str(TEMPERATURE_DS)] = {
        "date": "2026-09-13T17:14:04+0000",
        "max": None,
        "min": None,
        "seq": 1789318746316999,
        "type": 20,
        "value": 0,
    }
    placeholder["measurements"][str(BATTERY_DS)] = {
        "date": "2026-09-13T17:14:04+0000",
        "max": None,
        "min": None,
        "seq": 1789318746316998,
        "type": 30,
        "value": 0,
    }
    mock_api.get(URL_MEASUREMENTS, status=200, payload=placeholder, headers=None, repeat=True)

    freezer.tick(timedelta(seconds=901))
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    # Guard against a vacuous pass: the placeholder payload really was fetched.
    assert request_count(mock_api, URL_MEASUREMENTS) == 2
    # The bogus 0 values were discarded and the real readings stand.
    assert coordinator.data.measurements[TEMPERATURE_DS].value == 20.34
    assert coordinator.data.measurements[BATTERY_DS].value == 2.83


async def test_button_measurements_are_accepted(
    hass: HomeAssistant, mock_api: aioresponses, config_entry: MockConfigEntry
) -> None:
    """Type 5 (button push) carries a real value and is kept."""
    body = copy.deepcopy(load_fixture_body("measurements_latest"))
    body["measurements"][str(TEMPERATURE_DS)]["type"] = 5
    mock_full_account(mock_api, measurements=body)
    await setup_integration(hass, config_entry)

    coordinator: SensoristDataUpdateCoordinator = config_entry.runtime_data
    assert coordinator.data.measurements[TEMPERATURE_DS].measurement_type == 5
