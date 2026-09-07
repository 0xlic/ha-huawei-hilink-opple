"""Config flow for Huawei HiLink Opple lights."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow
from homeassistant.const import CONF_HOST, CONF_NAME
from homeassistant.core import HomeAssistant

try:
    from homeassistant.config_entries import ConfigFlowResult
except ImportError:  # Home Assistant before 2024.4
    from homeassistant.data_entry_flow import FlowResult as ConfigFlowResult

from .client import HiLinkAuthenticationError, HiLinkError, HiLinkLegacyClient
from .const import (
    CONF_AUTH_CODE,
    CONF_DEVICE_ID,
    CONF_MODEL,
    DEFAULT_MODEL,
    DEFAULT_NAME,
    DOMAIN,
)
from .credentials import (
    cache_credentials,
    clear_cached_credentials,
    get_cached_credentials,
    parse_credential_json,
)

CONF_CREDENTIALS_JSON = "credentials_json"
CONF_CLEAR_CREDENTIALS = "clear_cached_credentials"


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> None:
    """Test one credential with a read-only state request."""

    def validate() -> None:
        client = HiLinkLegacyClient(
            data[CONF_HOST], data[CONF_DEVICE_ID], data[CONF_AUTH_CODE]
        )
        client.create_session()
        client.read_state()

    await hass.async_add_executor_job(validate)


class HuaweiHiLinkOppleConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle Huawei HiLink Opple configuration."""

    VERSION = 2

    def _available_credentials(
        self, credentials: list[dict[str, str]]
    ) -> list[dict[str, str]]:
        configured = {
            entry.unique_id
            for entry in self._async_current_entries()
            if entry.unique_id is not None
        }
        return [
            record
            for record in credentials
            if record[CONF_DEVICE_ID] not in configured
        ]

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Use cached records immediately, otherwise request clipboard JSON."""
        if get_cached_credentials(self.hass):
            return await self.async_step_local()
        return await self.async_step_credentials(user_input)

    async def async_step_credentials(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Import credential records copied from the desktop helper."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                credentials = parse_credential_json(user_input[CONF_CREDENTIALS_JSON])
            except ValueError:
                errors["base"] = "invalid_credentials_json"
            else:
                cache_credentials(self.hass, credentials)
                if not self._available_credentials(credentials):
                    return self.async_abort(reason="no_new_devices")
                return await self.async_step_local()

        return self.async_show_form(
            step_id="credentials",
            data_schema=vol.Schema({vol.Required(CONF_CREDENTIALS_JSON): str}),
            errors=errors,
        )

    async def async_step_local(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Match a local IP against every unused cached credential."""
        errors: dict[str, str] = {}
        credentials = get_cached_credentials(self.hass)
        if not credentials:
            return await self.async_step_credentials()

        available = self._available_credentials(credentials)
        if not available:
            return self.async_abort(reason="no_new_devices")

        if user_input is not None:
            if user_input.get(CONF_CLEAR_CREDENTIALS):
                clear_cached_credentials(self.hass)
                return await self.async_step_credentials()

            name = user_input.get(CONF_NAME, "").strip()
            host = user_input.get(CONF_HOST, "").strip()
            if not name or not host:
                errors["base"] = "name_and_host_required"
                return self._show_local_form(available, errors)
            matched: dict[str, str] | None = None
            for record in available:
                candidate = {**record, CONF_HOST: host}
                try:
                    await validate_input(self.hass, candidate)
                except (HiLinkAuthenticationError, HiLinkError, OSError, ValueError):
                    continue
                matched = candidate
                break

            if matched is None:
                errors["base"] = "credentials_not_matched"
            else:
                await self.async_set_unique_id(matched[CONF_DEVICE_ID])
                self._abort_if_unique_id_configured(updates={CONF_HOST: host})
                data = {
                    CONF_NAME: name,
                    CONF_HOST: host,
                    CONF_DEVICE_ID: matched[CONF_DEVICE_ID],
                    CONF_AUTH_CODE: matched[CONF_AUTH_CODE],
                    CONF_MODEL: matched.get(CONF_MODEL, DEFAULT_MODEL),
                }
                return self.async_create_entry(title=data[CONF_NAME], data=data)

        return self._show_local_form(available, errors)

    def _show_local_form(
        self, available: list[dict[str, str]], errors: dict[str, str]
    ) -> ConfigFlowResult:
        """Show the local match form, including an immediate cache-clear action."""
        suggested_name = next(
            (record["name"] for record in available if record.get("name")), DEFAULT_NAME
        )
        return self.async_show_form(
            step_id="local",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_NAME, default=suggested_name): str,
                    vol.Optional(CONF_HOST, default=""): str,
                    vol.Optional(CONF_CLEAR_CREDENTIALS, default=False): bool,
                }
            ),
            errors=errors,
            description_placeholders={"count": str(len(available))},
        )

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Keep a direct form for recovery and advanced use."""
        errors: dict[str, str] = {}
        if user_input is not None:
            data = dict(user_input)
            data[CONF_DEVICE_ID] = data[CONF_DEVICE_ID].strip()
            data[CONF_AUTH_CODE] = data[CONF_AUTH_CODE].strip()
            await self.async_set_unique_id(data[CONF_DEVICE_ID])
            self._abort_if_unique_id_configured(updates={CONF_HOST: data[CONF_HOST]})
            try:
                await validate_input(self.hass, data)
            except HiLinkAuthenticationError:
                errors["base"] = "invalid_auth"
            except (HiLinkError, OSError, ValueError):
                errors["base"] = "cannot_connect"
            else:
                return self.async_create_entry(title=data[CONF_NAME], data=data)

        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default=DEFAULT_NAME): str,
                vol.Required(CONF_HOST): str,
                vol.Required(CONF_DEVICE_ID): str,
                vol.Required(CONF_AUTH_CODE): str,
                vol.Optional(CONF_MODEL, default=DEFAULT_MODEL): str,
            }
        )
        return self.async_show_form(step_id="manual", data_schema=schema, errors=errors)
