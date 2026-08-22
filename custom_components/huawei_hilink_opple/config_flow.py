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


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> None:
    """Test the auth code with a read-only state request."""

    def validate() -> None:
        client = HiLinkLegacyClient(
            data[CONF_HOST], data[CONF_DEVICE_ID], data[CONF_AUTH_CODE]
        )
        client.create_session()
        client.read_state()

    await hass.async_add_executor_job(validate)


class HuaweiHiLinkOppleConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle Huawei HiLink Opple configuration."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial setup step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input[CONF_AUTH_CODE] = user_input[CONF_AUTH_CODE].strip()
            await self.async_set_unique_id(user_input[CONF_DEVICE_ID].strip())
            self._abort_if_unique_id_configured(updates={CONF_HOST: user_input[CONF_HOST]})
            try:
                await validate_input(self.hass, user_input)
            except HiLinkAuthenticationError:
                errors["base"] = "invalid_auth"
            except (HiLinkError, OSError, ValueError):
                errors["base"] = "cannot_connect"
            else:
                return self.async_create_entry(title=user_input[CONF_NAME], data=user_input)

        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default=DEFAULT_NAME): str,
                vol.Required(CONF_HOST): str,
                vol.Required(CONF_DEVICE_ID): str,
                vol.Required(CONF_AUTH_CODE): str,
                vol.Optional(CONF_MODEL, default=DEFAULT_MODEL): str,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)
