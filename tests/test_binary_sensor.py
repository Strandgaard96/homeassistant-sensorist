"""Tests for the Sensorist gateway connectivity entity."""

from __future__ import annotations

import copy

from homeassistant.const import ATTR_DEVICE_CLASS, STATE_OFF, STATE_ON, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.sensorist.const import DOMAIN

from .conftest import GATEWAY_ID, load_fixture_body, mock_full_account, setup_integration

UNIQUE_ID = f"gateway_{GATEWAY_ID}_connectivity"


def entity_id_for(hass: HomeAssistant) -> str:
    """Look the connectivity entity up by unique id."""
    entity_id = er.async_get(hass).async_get_entity_id("binary_sensor", DOMAIN, UNIQUE_ID)
    assert entity_id is not None
    return entity_id


async def test_connected_gateway(
    hass: HomeAssistant, mock_api: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """A null disconnected_date means the gateway is online."""
    mock_full_account(mock_api)
    await setup_integration(hass, config_entry)

    entity_id = entity_id_for(hass)
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == STATE_ON
    assert state.attributes[ATTR_DEVICE_CLASS] == "connectivity"
    assert "disconnected_since" not in state.attributes

    entry = er.async_get(hass).async_get(entity_id)
    assert entry is not None
    assert entry.entity_category is EntityCategory.DIAGNOSTIC


async def test_disconnected_gateway_reports_when(
    hass: HomeAssistant, mock_api: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """A populated disconnected_date turns the entity off and is exposed."""
    gateways = copy.deepcopy(load_fixture_body("gateways_with_slaves"))
    gateways["gateways"][0]["disconnected_date"] = "2026-09-13T12:00:00+0000"
    mock_full_account(mock_api, gateways=gateways)
    await setup_integration(hass, config_entry)

    state = hass.states.get(entity_id_for(hass))
    assert state is not None
    assert state.state == STATE_OFF
    assert state.attributes["disconnected_since"] == "2026-09-13T12:00:00+00:00"


async def test_one_entity_per_gateway(
    hass: HomeAssistant, mock_api: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """The single gateway in the fixtures yields exactly one connectivity entity."""
    mock_full_account(mock_api)
    await setup_integration(hass, config_entry)

    registry = er.async_get(hass)
    entities = er.async_entries_for_config_entry(registry, config_entry.entry_id)
    assert len([e for e in entities if e.domain == "binary_sensor"]) == 1
