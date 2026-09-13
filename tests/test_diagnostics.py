"""Tests for Sensorist diagnostics."""

from __future__ import annotations

import json

from aioresponses import aioresponses
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sensorist.diagnostics import async_get_config_entry_diagnostics

from .conftest import mock_full_account, setup_integration


async def test_diagnostics_redacts_credentials(
    hass: HomeAssistant, mock_api: aioresponses, config_entry: MockConfigEntry
) -> None:
    """The password and email never appear in a diagnostics dump."""
    mock_full_account(mock_api)
    await setup_integration(hass, config_entry)

    diagnostics = await async_get_config_entry_diagnostics(hass, config_entry)
    serialised = json.dumps(diagnostics, default=str)

    assert "hunter2" not in serialised
    assert "redacted@example.com" not in serialised
    assert diagnostics["entry"]["data"]["password"] == "**REDACTED**"


async def test_diagnostics_excludes_network_addresses(
    hass: HomeAssistant, mock_api: aioresponses, config_entry: MockConfigEntry
) -> None:
    """Gateway IP addresses are dropped by the coordinator and cannot leak."""
    mock_full_account(mock_api)
    await setup_integration(hass, config_entry)

    diagnostics = await async_get_config_entry_diagnostics(hass, config_entry)
    serialised = json.dumps(diagnostics, default=str)

    assert "203.0.113.10" not in serialised
    assert "192.168.1.10" not in serialised


async def test_diagnostics_includes_coordinator_data(
    hass: HomeAssistant, mock_api: aioresponses, config_entry: MockConfigEntry
) -> None:
    """The dump carries enough to debug a mapping problem."""
    mock_full_account(mock_api)
    await setup_integration(hass, config_entry)

    diagnostics = await async_get_config_entry_diagnostics(hass, config_entry)

    assert diagnostics["coordinator"]["last_update_success"] is True
    assert diagnostics["coordinator"]["update_interval"] == 900.0
    assert len(diagnostics["gateways"]) == 1
    assert len(diagnostics["data_sources"]) == 8
    assert len(diagnostics["measurements"]) == 8
