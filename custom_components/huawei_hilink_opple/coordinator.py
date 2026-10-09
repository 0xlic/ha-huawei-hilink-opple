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

from .client import (
    DEFAULT_REQUEST_TIMEOUT,
    HiLinkError,
    HiLinkLegacyClient,
    LightState,
)
from .const import (
    CONF_AUTH_CODE,
    CONF_DEVICE_ID,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    FAST_REFRESH_TIMEOUT,
    RECOVERY_WINDOW_SECONDS,
)

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
        self._recovery_until = 0.0

    def _start_recovery(self) -> None:
        """Extend this lamp's recovery window before waiting for the device lock."""
        self._recovery_until = time.monotonic() + RECOVERY_WINDOW_SECONDS

    def _in_recovery(self) -> bool:
        return time.monotonic() < self._recovery_until

    def _new_client(
        self, *, timeout: float = DEFAULT_REQUEST_TIMEOUT
    ) -> HiLinkLegacyClient:
        return HiLinkLegacyClient(
            self.entry.data[CONF_HOST],
            self.entry.data[CONF_DEVICE_ID],
            self.entry.data[CONF_AUTH_CODE],
            timeout=timeout,
        )

    def _sync_read(self) -> LightState:
        # Select the policy in the worker, after acquiring the device lock. A
        # refresh can open the recovery window while this poll is queued.
        if self._in_recovery():
            return self._sync_fast_read()
        last_error: Exception | None = None
        for _attempt in range(2):
            try:
                if self._client is None:
                    self._client = self._new_client()
                    self._client.create_session()
                if self._in_recovery():
                    self._client.timeout = FAST_REFRESH_TIMEOUT
                state = self._client.read_state()
                self._client.timeout = DEFAULT_REQUEST_TIMEOUT
                return state
            except HiLinkError as exc:
                last_error = exc
                self._client = None
                # An in-flight socket read cannot be safely interrupted. If a
                # refresh arrived meanwhile, do not add another long attempt.
                if self._in_recovery():
                    break
        raise last_error or HiLinkError("State read failed")

    def _sync_fast_read(self) -> LightState:
        """Read once with a fresh session and a short recovery timeout."""
        self._client = self._new_client(timeout=FAST_REFRESH_TIMEOUT)
        try:
            self._client.create_session()
            state = self._client.read_state()
        except HiLinkError:
            self._client = None
            raise
        self._client.timeout = DEFAULT_REQUEST_TIMEOUT
        return state

    async def _async_update_data(self) -> LightState:
        async with self._lock:
            try:
                state = await self.hass.async_add_executor_job(self._sync_read)
            except HiLinkError as exc:
                raise UpdateFailed(str(exc)) from exc
            self._recovery_until = 0.0
            return state

    async def async_force_refresh(self) -> None:
        """Open recovery immediately and explicitly read through a new session."""
        self._start_recovery()
        error: UpdateFailed | None = None
        state: LightState | None = None
        async with self._lock:
            try:
                state = await self.hass.async_add_executor_job(self._sync_fast_read)
            except HiLinkError as exc:
                error = UpdateFailed(str(exc))
            else:
                self._recovery_until = 0.0
        if error is not None:
            self.async_set_update_error(error)
            return
        if state is not None:
            self.async_set_updated_data(state)

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
            self._recovery_until = 0.0
        self.async_set_updated_data(state)
