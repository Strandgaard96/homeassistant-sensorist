"""Config flow for the Sensorist integration."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import SensoristApi, SensoristAuthError, SensoristConnectionError, SensoristError
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): str,
        vol.Required(CONF_PASSWORD): str,
    }
)

STEP_REAUTH_SCHEMA = vol.Schema({vol.Required(CONF_PASSWORD): str})


class SensoristConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Sensorist."""

    VERSION = 1

    async def _async_validate(self, email: str, password: str) -> tuple[str | None, str | None]:
        """Return (unique_id, error_key) for a credential pair."""
        api = SensoristApi(async_get_clientsession(self.hass), email, password)
        try:
            user = await api.async_get_user()
        except SensoristAuthError:
            return None, "invalid_auth"
        except SensoristConnectionError:
            return None, "cannot_connect"
        except SensoristError:
            _LOGGER.exception("Unexpected error validating Sensorist credentials")
            return None, "unknown"

        user_id = user.get("id")
        # The account always carried an id during discovery; fall back to the
        # email so a missing id cannot break setup.
        unique_id = str(user_id) if user_id is not None else email.lower()
        return unique_id, None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            email = user_input[CONF_EMAIL]
            unique_id, error = await self._async_validate(email, user_input[CONF_PASSWORD])
            if error is not None:
                errors["base"] = error
            else:
                assert unique_id is not None
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=email, data=user_input)

        return self.async_show_form(step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Handle re-authentication after the API rejected our credentials."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Prompt for a new password, keeping the existing email."""
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        email = entry.data[CONF_EMAIL]

        if user_input is not None:
            unique_id, error = await self._async_validate(email, user_input[CONF_PASSWORD])
            if error is not None:
                errors["base"] = error
            else:
                assert unique_id is not None
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_mismatch(reason="wrong_account")
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]}
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=STEP_REAUTH_SCHEMA,
            description_placeholders={"email": email},
            errors=errors,
        )
