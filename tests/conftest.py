"""Fixtures for the Sensorist tests."""

from __future__ import annotations

import json
import re
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from aioresponses import aioresponses
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant, State
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sensorist.const import DOMAIN

FIXTURE_DIR = Path(__file__).parent / "fixtures"

# The discovery account: one gateway, two sensors, four data sources each.
USER_ID = "14886"
GATEWAY_ID = 7325
SENSOR_ONE_ID = 57950
SENSOR_TWO_ID = 57951
TEMPERATURE_DS = 220200
HUMIDITY_DS = 220201
BATTERY_DS = 220199
WIRELESS_DS = 220198

# The moment the fixtures were captured. Measurement ages are only meaningful
# relative to this, so every availability test pins the clock here.
CAPTURED_AT = "2026-09-13T17:05:19+00:00"

URL_USERS = re.compile(r"^https://api\.sensorist\.com/v1/users(\?.*)?$")
URL_GATEWAYS = re.compile(r"^https://api\.sensorist\.com/v1/gateways(\?.*)?$")
URL_MEASUREMENTS = re.compile(r"^https://api\.sensorist\.com/v1/measurements(\?.*)?$")

CACHE_HEADERS = {"cache-control": "max-age=900, private"}


def load_fixture_body(name: str) -> dict[str, Any]:
    """Return the recorded response body for a captured endpoint."""
    raw = json.loads((FIXTURE_DIR / f"{name}.json").read_text(encoding="utf-8"))
    body: dict[str, Any] = raw["body"]
    return body


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(
    enable_custom_integrations: None,
) -> Generator[None]:
    """Make the custom integration importable in every test."""
    yield


@pytest.fixture
def users_body() -> dict[str, Any]:
    """Return the captured /users body."""
    return load_fixture_body("users")


@pytest.fixture
def gateways_body() -> dict[str, Any]:
    """Return the captured /gateways body."""
    return load_fixture_body("gateways_with_slaves")


@pytest.fixture
def measurements_body() -> dict[str, Any]:
    """Return the captured /measurements body."""
    return load_fixture_body("measurements_latest")


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """Return a config entry matching the discovery account."""
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=USER_ID,
        title="redacted@example.com",
        data={CONF_EMAIL: "redacted@example.com", CONF_PASSWORD: "hunter2"},
    )


@pytest.fixture
def mock_api() -> Generator[aioresponses]:
    """Intercept every outgoing aiohttp request."""
    with aioresponses() as mocked:
        yield mocked


def mock_full_account(
    mocked: aioresponses,
    users: dict[str, Any] | None = None,
    gateways: dict[str, Any] | None = None,
    measurements: dict[str, Any] | None = None,
    repeat: bool = True,
) -> None:
    """Register the three endpoints a full setup calls."""
    if users is None:
        users = load_fixture_body("users")
    if gateways is None:
        gateways = load_fixture_body("gateways_with_slaves")
    if measurements is None:
        measurements = load_fixture_body("measurements_latest")

    mocked.get(URL_USERS, status=200, payload=users, headers=CACHE_HEADERS, repeat=repeat)
    mocked.get(URL_GATEWAYS, status=200, payload=gateways, headers=CACHE_HEADERS, repeat=repeat)
    mocked.get(
        URL_MEASUREMENTS,
        status=200,
        payload=measurements,
        headers=CACHE_HEADERS,
        repeat=repeat,
    )


def request_count(mocked: aioresponses, pattern: re.Pattern[str]) -> int:
    """Count how many requests matched a URL pattern."""
    total = 0
    for (_method, url), calls in mocked.requests.items():
        if pattern.match(str(url)):
            total += len(calls)
    return total


def get_state(hass: HomeAssistant, entity_id: str) -> State:
    """Return a state that must exist, so tests can assert on it directly."""
    state = hass.states.get(entity_id)
    assert state is not None, f"{entity_id} has no state"
    return state


async def setup_integration(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Add the entry to Home Assistant and run a full setup."""
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
