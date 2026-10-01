"""Tests for the Sensorist config flow."""

from __future__ import annotations

import aiohttp
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.sensorist.const import DOMAIN

from .conftest import (
    CACHE_HEADERS,
    URL_USERS,
    USER_ID,
    load_fixture_body,
    mock_full_account,
    mock_get,
)

CREDENTIALS = {CONF_EMAIL: "redacted@example.com", CONF_PASSWORD: "hunter2"}


async def test_user_flow_success(hass: HomeAssistant, mock_api: AiohttpClientMocker) -> None:
    """A valid account creates an entry keyed on the Sensorist user id."""
    mock_full_account(mock_api)

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], CREDENTIALS)
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "redacted@example.com"
    assert result["data"] == CREDENTIALS
    assert result["result"].unique_id == USER_ID


async def test_user_flow_invalid_auth_then_recovers(
    hass: HomeAssistant, mock_api: AiohttpClientMocker
) -> None:
    """Bad credentials show invalid_auth and the form stays usable."""
    mock_get(mock_api, URL_USERS, status=401)

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], CREDENTIALS)

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}

    mock_full_account(mock_api)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], CREDENTIALS)
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_flow_cannot_connect_then_recovers(
    hass: HomeAssistant, mock_api: AiohttpClientMocker
) -> None:
    """A network failure shows cannot_connect, and a retry succeeds."""
    mock_get(mock_api, URL_USERS, exc=aiohttp.ClientError("boom"))

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], CREDENTIALS)

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}

    mock_full_account(mock_api)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], CREDENTIALS)
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_flow_unknown_error_then_recovers(
    hass: HomeAssistant, mock_api: AiohttpClientMocker
) -> None:
    """An unusable response shows the generic error, and a retry succeeds."""
    mock_get(mock_api, URL_USERS, status=200, json={"code": 200}, headers=CACHE_HEADERS)

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], CREDENTIALS)

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "unknown"}

    mock_full_account(mock_api)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], CREDENTIALS)
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_already_configured(
    hass: HomeAssistant, mock_api: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """The same Sensorist account cannot be added twice."""
    config_entry.add_to_hass(hass)
    mock_full_account(mock_api)

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], CREDENTIALS)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth_updates_password(
    hass: HomeAssistant, mock_api: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """Reauth asks only for the password and writes it back to the entry."""
    config_entry.add_to_hass(hass)
    mock_full_account(mock_api)

    result = await config_entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"
    # Only the password is asked for; the email is carried over.
    assert CONF_EMAIL not in result["data_schema"].schema

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: "new-password"}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data[CONF_PASSWORD] == "new-password"
    assert config_entry.data[CONF_EMAIL] == "redacted@example.com"


async def test_reauth_invalid_auth_then_recovers(
    hass: HomeAssistant, mock_api: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """A still-wrong password keeps the reauth form open for another try."""
    config_entry.add_to_hass(hass)
    mock_get(mock_api, URL_USERS, status=403)

    result = await config_entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: "still-wrong"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}

    mock_full_account(mock_api)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: "new-password"}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data[CONF_PASSWORD] == "new-password"


async def test_reauth_rejects_different_account(
    hass: HomeAssistant, mock_api: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """Credentials for another Sensorist account must not hijack the entry."""
    config_entry.add_to_hass(hass)
    other = load_fixture_body("users")
    other["user"]["id"] = 99999
    mock_get(mock_api, URL_USERS, status=200, json=other, headers=CACHE_HEADERS)

    result = await config_entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: "someone-elses"}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_account"
