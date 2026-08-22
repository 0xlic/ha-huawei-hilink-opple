"""Data coordinator for Huawei HiLink Opple lights."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .client import HiLinkError, HiLinkLegacyClient, LightState
from .const import CONF_AUTH_CODE, CONF_DEVICE_ID, DEFAULT_POLL_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)


class HiLinkCoordinator(DataUpdateCoordinator[LightState]):
    """Serialize access to one lamp and recreate expired sessions."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            logger=_LOGGER,
            name=f"{DOMAIN}_{entry.data[CONF_DEVICE_ID]}",
            update_interval=timedelta(seconds=DEFAULT_POLL_INTERVAL),
            always_update=False,
        )
        self.entry = entry
        self._lock = asyncio.Lock()
        self._client: HiLinkLegacyClient | None = None

    def _new_client(self) -> HiLinkLegacyClient:
        return HiLinkLegacyClient(
            self.entry.data[CONF_HOST],
            self.entry.data[CONF_DEVICE_ID],
            self.entry.data[CONF_AUTH_CODE],
        )

    def _sync_read(self) -> LightState:
        last_error: Exception | None = None
        for _attempt in range(2):
            try:
                if self._client is None:
                    self._client = self._new_client()
                    self._client.create_session()
                return self._client.read_state()
            except HiLinkError as exc:
                last_error = exc
                self._client = None
        raise last_error or HiLinkError("State read failed")

    async def _async_update_data(self) -> LightState:
        async with self._lock:
            try:
                return await self.hass.async_add_executor_job(self._sync_read)
            except HiLinkError as exc:
                raise UpdateFailed(str(exc)) from exc

    def _sync_commands(self, commands: list[tuple[str, dict[str, Any]]]) -> LightState:
        last_error: Exception | None = None
        for _attempt in range(2):
            try:
                if self._client is None:
                    self._client = self._new_client()
                    self._client.create_session()
                for index, (service_id, data) in enumerate(commands):
                    self._client.set_service(service_id, data)
                    if (
                        index == 0
                        and service_id == "switch"
                        and data.get("on") == 1
                        and len(commands) > 1
                    ):
                        # The lamp reapplies its wall-switch preset shortly after
                        # power-on. Let that settle before brightness/CCT commands.
                        time.sleep(0.25)
                return self._client.read_state()
            except HiLinkError as exc:
                last_error = exc
                self._client = None
        raise last_error or HiLinkError("Control failed")

    async def async_send(self, commands: list[tuple[str, dict[str, Any]]]) -> None:
        """Send commands and immediately publish the confirmed device state."""
        async with self._lock:
            try:
                state = await self.hass.async_add_executor_job(self._sync_commands, commands)
            except HiLinkError as exc:
                raise UpdateFailed(str(exc)) from exc
        self.async_set_updated_data(state)
