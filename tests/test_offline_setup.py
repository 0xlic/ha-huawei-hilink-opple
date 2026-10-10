"""Offline entry loading and unknown-state properties with minimal HA doubles."""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest

ROOT = Path(__file__).parents[1] / "custom_components/huawei_hilink_opple"


@pytest.fixture
def modules(monkeypatch):
    names = (
        "homeassistant", "homeassistant.components", "homeassistant.components.light",
        "homeassistant.config_entries", "homeassistant.const", "homeassistant.core",
        "homeassistant.helpers", "homeassistant.helpers.device_registry",
        "homeassistant.helpers.entity_platform", "homeassistant.helpers.update_coordinator",
    )
    for name in names:
        monkeypatch.setitem(sys.modules, name, ModuleType(name))
    sys.modules["homeassistant.config_entries"].ConfigEntry = object
    sys.modules["homeassistant.const"].CONF_NAME = "name"
    sys.modules["homeassistant.core"].HomeAssistant = object
    light = sys.modules["homeassistant.components.light"]
    light.ATTR_BRIGHTNESS = "brightness"
    light.ATTR_COLOR_TEMP_KELVIN = "color_temp_kelvin"
    light.ColorMode = SimpleNamespace(COLOR_TEMP="color_temp")
    light.LightEntity = type("LightEntity", (), {})
    sys.modules["homeassistant.helpers.device_registry"].DeviceInfo = dict
    sys.modules["homeassistant.helpers.entity_platform"].AddConfigEntryEntitiesCallback = object

    class CoordinatorEntity:
        def __class_getitem__(cls, item):
            return cls

        def __init__(self, coordinator):
            self.coordinator = coordinator

        @property
        def available(self):
            return self.coordinator.last_update_success

    sys.modules["homeassistant.helpers.update_coordinator"].CoordinatorEntity = CoordinatorEntity
    coordinator_module = ModuleType("opple_offline_test.coordinator")

    class Coordinator:
        offline = True

        def __init__(self, hass, entry):
            self.data = None
            self.last_update_success = False
            self.async_refresh = AsyncMock(side_effect=self.refresh)
            self.async_config_entry_first_refresh = AsyncMock(
                side_effect=AssertionError("Offline loading must not use first_refresh")
            )

        async def refresh(self):
            if not self.offline:
                self.data = SimpleNamespace(is_on=True, brightness=100, color_temp_kelvin=2700)
                self.last_update_success = True

    coordinator_module.HiLinkCoordinator = Coordinator
    monkeypatch.setitem(sys.modules, "opple_offline_test.coordinator", coordinator_module)
    for name in ("const", "light"):
        monkeypatch.setitem(sys.modules, f"opple_offline_test.{name}", None)
        del sys.modules[f"opple_offline_test.{name}"]
    spec = importlib.util.spec_from_file_location(
        "opple_offline_test", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
    )
    package = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, package)
    spec.loader.exec_module(package)
    entity_module = importlib.import_module("opple_offline_test.light")
    return package, entity_module, Coordinator


@pytest.mark.parametrize("offline", [True, False])
def test_entry_loads_even_when_first_read_fails(modules, offline):
    setup, light, coordinator_class = modules
    coordinator_class.offline = offline
    entry = SimpleNamespace(entry_id="entry", data={"device_id": "device", "name": "lamp"})
    hass = SimpleNamespace(data={}, config_entries=SimpleNamespace(
        async_forward_entry_setups=AsyncMock(),
        async_unload_platforms=AsyncMock(return_value=True),
    ))

    async def scenario():
        assert await setup.async_setup_entry(hass, entry)
        coordinator = hass.data[setup.DOMAIN][entry.entry_id]
        coordinator.async_refresh.assert_awaited_once()
        coordinator.async_config_entry_first_refresh.assert_not_awaited()
        hass.config_entries.async_forward_entry_setups.assert_awaited_once_with(entry, ["light"])
        entity = light.HuaweiHiLinkOppleLight(coordinator, entry)
        if offline:
            assert not entity.available
            assert entity.is_on is None
            assert entity.brightness is None
            assert entity.color_temp_kelvin is None
            # The same entity recovers when a later refresh receives state.
            coordinator_class.offline = False
            await coordinator.async_refresh()
        assert entity.available
        assert entity.is_on is True
        assert entity.brightness == 255
        assert entity.color_temp_kelvin == 2700
        assert await setup.async_unload_entry(hass, entry)
        assert entry.entry_id not in hass.data[setup.DOMAIN]

    asyncio.run(scenario())
