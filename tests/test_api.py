"""Tests for the Sensorist API client, chiefly its max-age caching."""

from __future__ import annotations

import asyncio

import aiohttp
import pytest
from aioresponses import aioresponses
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from custom_components.sensorist.api import (
    SensoristApi,
    SensoristApiError,
    SensoristAuthError,
    SensoristConnectionError,
)

from .conftest import (
    CACHE_HEADERS,
    GATEWAY_ID,
    URL_MEASUREMENTS,
    URL_USERS,
    load_fixture_body,
    request_count,
)


def build_api(hass: HomeAssistant) -> SensoristApi:
    """Return a client bound to the Home Assistant shared session."""
    return SensoristApi(async_get_clientsession(hass), "user@example.com", "hunter2")


async def test_max_age_cache_prevents_second_request(
    hass: HomeAssistant, mock_api: aioresponses
) -> None:
    """A repeat call inside the max-age window must not touch the network."""
    mock_api.get(
        URL_USERS,
        status=200,
        payload=load_fixture_body("users"),
        headers=CACHE_HEADERS,
    )
    api = build_api(hass)

    first = await api.async_get_user()
    second = await api.async_get_user()

    assert first == second
    assert request_count(mock_api, URL_USERS) == 1


async def test_cache_expires_after_max_age(
    hass: HomeAssistant, mock_api: aioresponses, freezer: FrozenDateTimeFactory
) -> None:
    """Once max-age has elapsed the client fetches again."""
    body = load_fixture_body("users")
    mock_api.get(URL_USERS, status=200, payload=body, headers=CACHE_HEADERS, repeat=True)
    api = build_api(hass)

    await api.async_get_user()
    # The cache keys off time.monotonic(), which freezegun also patches.
    freezer.tick(901)
    await api.async_get_user()

    assert request_count(mock_api, URL_USERS) == 2


async def test_concurrent_calls_share_one_request(
    hass: HomeAssistant, mock_api: aioresponses
) -> None:
    """Two simultaneous calls for the same URL must not race into two fetches."""
    mock_api.get(
        URL_USERS,
        status=200,
        payload=load_fixture_body("users"),
        headers=CACHE_HEADERS,
    )
    api = build_api(hass)

    await asyncio.gather(api.async_get_user(), api.async_get_user())

    assert request_count(mock_api, URL_USERS) == 1


async def test_max_age_recorded_per_path(hass: HomeAssistant, mock_api: aioresponses) -> None:
    """The client remembers the max-age it saw so the coordinator can honour it."""
    mock_api.get(
        URL_MEASUREMENTS,
        status=200,
        payload=load_fixture_body("measurements_latest"),
        headers={"cache-control": "max-age=1800, private"},
    )
    api = build_api(hass)

    await api.async_get_latest(GATEWAY_ID, [220200])

    assert api.max_age_for("/measurements") == 1800


async def test_missing_cache_control_falls_back_to_body(
    hass: HomeAssistant, mock_api: aioresponses
) -> None:
    """With no header, the body's own max_age field is used."""
    mock_api.get(URL_USERS, status=200, payload=load_fixture_body("users"))
    api = build_api(hass)

    await api.async_get_user()

    assert api.max_age_for("/users") == 900


@pytest.mark.parametrize("status", [401, 403])
async def test_auth_error(hass: HomeAssistant, mock_api: aioresponses, status: int) -> None:
    """Rejected credentials raise SensoristAuthError."""
    mock_api.get(URL_USERS, status=status)
    api = build_api(hass)

    with pytest.raises(SensoristAuthError):
        await api.async_get_user()


async def test_server_error_is_connection_error(
    hass: HomeAssistant, mock_api: aioresponses
) -> None:
    """A 5xx is treated as a transient connection problem."""
    mock_api.get(URL_USERS, status=503)
    api = build_api(hass)

    with pytest.raises(SensoristConnectionError):
        await api.async_get_user()


async def test_network_failure_is_connection_error(
    hass: HomeAssistant, mock_api: aioresponses
) -> None:
    """An aiohttp client error is wrapped, never leaked."""
    mock_api.get(URL_USERS, exception=aiohttp.ClientError("boom"))
    api = build_api(hass)

    with pytest.raises(SensoristConnectionError):
        await api.async_get_user()


async def test_timeout_is_connection_error(hass: HomeAssistant, mock_api: aioresponses) -> None:
    """A timeout is wrapped as a connection error, not left to bubble."""
    mock_api.get(URL_USERS, exception=TimeoutError)
    api = build_api(hass)

    with pytest.raises(SensoristConnectionError):
        await api.async_get_user()


async def test_unexpected_status_is_api_error(hass: HomeAssistant, mock_api: aioresponses) -> None:
    """Anything else the API returns raises SensoristApiError."""
    mock_api.get(URL_USERS, status=418)
    api = build_api(hass)

    with pytest.raises(SensoristApiError):
        await api.async_get_user()


async def test_missing_payload_key_is_api_error(
    hass: HomeAssistant, mock_api: aioresponses
) -> None:
    """A 200 without the expected payload key is still an error."""
    mock_api.get(URL_USERS, status=200, payload={"code": 200}, headers=CACHE_HEADERS)
    api = build_api(hass)

    with pytest.raises(SensoristApiError):
        await api.async_get_user()


async def test_empty_data_source_list_skips_request(
    hass: HomeAssistant, mock_api: aioresponses
) -> None:
    """Asking for no data sources must not produce a pointless API call."""
    api = build_api(hass)

    assert await api.async_get_latest(GATEWAY_ID, []) == {}
    assert request_count(mock_api, URL_MEASUREMENTS) == 0
