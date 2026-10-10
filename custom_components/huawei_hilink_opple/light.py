"""Light entity for Huawei HiLink Opple ceiling lights."""

from __future__ import annotations

from typing import Any, ClassVar

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ColorMode,
    LightEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo

try:
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
except ImportError:  # Home Assistant before 2024.5
    from homeassistant.helpers.entity_platform import (
        AddEntitiesCallback as AddConfigEntryEntitiesCallback,
    )
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_DEVICE_ID,
    CONF_MODEL,
    DOMAIN,
    MAX_COLOR_TEMP_KELVIN,
    MIN_COLOR_TEMP_KELVIN,
)
from .coordinator import HiLinkCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the light platform."""
    coordinator: HiLinkCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([HuaweiHiLinkOppleLight(coordinator, entry)])


class HuaweiHiLinkOppleLight(CoordinatorEntity[HiLinkCoordinator], LightEntity):
    """Representation of one Huawei Select Opple light."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_supported_color_modes: ClassVar[set[ColorMode]] = {ColorMode.COLOR_TEMP}
    _attr_color_mode = ColorMode.COLOR_TEMP
    _attr_min_color_temp_kelvin = MIN_COLOR_TEMP_KELVIN
    _attr_max_color_temp_kelvin = MAX_COLOR_TEMP_KELVIN

    def __init__(self, coordinator: HiLinkCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        device_id = entry.data[CONF_DEVICE_ID]
        self._attr_unique_id = device_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            name=entry.data[CONF_NAME],
            manufacturer="Huawei Select / Opple",
            model=entry.data.get(CONF_MODEL),
        )

    async def async_update(self) -> None:
        """Force a fresh short-timeout session for explicit HA refreshes."""
        await self.coordinator.async_force_refresh()

    @property
    def is_on(self) -> bool | None:
        """Return whether the light is on, if any state has been received."""
        state = self.coordinator.data
        return state.is_on if state is not None else None

    @property
    def brightness(self) -> int | None:
        """Return brightness in Home Assistant's 1..255 scale, when known."""
        state = self.coordinator.data
        return max(1, round(state.brightness * 255 / 100)) if state is not None else None

    @property
    def color_temp_kelvin(self) -> int | None:
        """Return the current color temperature in kelvin, when known."""
        state = self.coordinator.data
        return state.color_temp_kelvin if state is not None else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on and apply optional brightness/color temperature."""
        commands: list[tuple[str, dict[str, Any]]] = []
        if not self.is_on:
            commands.append(("switch", {"on": 1}))
        if ATTR_BRIGHTNESS in kwargs:
            percent = max(1, min(100, round(kwargs[ATTR_BRIGHTNESS] * 100 / 255)))
            commands.append(("brightness", {"brightness": percent}))
        if ATTR_COLOR_TEMP_KELVIN in kwargs:
            kelvin = max(
                MIN_COLOR_TEMP_KELVIN,
                min(MAX_COLOR_TEMP_KELVIN, int(kwargs[ATTR_COLOR_TEMP_KELVIN])),
            )
            commands.append(("cct", {"colorTemperature": kelvin}))
        if commands:
            await self.coordinator.async_send(commands)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the light off."""
        await self.coordinator.async_send([("switch", {"on": 0})])
