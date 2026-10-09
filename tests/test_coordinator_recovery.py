"""Recovery policy tests using minimal HA doubles, without a running HA."""

from __future__ import annotations

import asyncio
import importlib
import sys
import time
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


@pytest.fixture
def module(monkeypatch):
    package = ModuleType("opple_recovery_test")
    package.__path__ = [str(Path(__file__).parents[1] / "custom_components/huawei_hilink_opple")]
    monkeypatch.setitem(sys.modules, package.__name__, package)
    for name in (
        "homeassistant", "homeassistant.config_entries", "homeassistant.const",
        "homeassistant.core", "homeassistant.helpers", "homeassistant.helpers.update_coordinator",
    ):
        monkeypatch.setitem(sys.modules, name, ModuleType(name))
    sys.modules["homeassistant.config_entries"].ConfigEntry = object
    sys.modules["homeassistant.const"].CONF_HOST = "host"
    sys.modules["homeassistant.core"].HomeAssistant = object

    class Coordinator:
        def __class_getitem__(cls, item):
            return cls

        def __init__(self, hass, **kwargs):
            self.hass = hass

        def async_set_updated_data(self, state):
            self.published = state

        def async_set_update_error(self, error):
            self.error = error

    ha = sys.modules["homeassistant.helpers.update_coordinator"]
    ha.DataUpdateCoordinator = Coordinator
    ha.UpdateFailed = type("UpdateFailed", (Exception,), {})
    for name in ("client", "const", "coordinator"):
        monkeypatch.setitem(sys.modules, f"opple_recovery_test.{name}", None)
        del sys.modules[f"opple_recovery_test.{name}"]
    return importlib.import_module("opple_recovery_test.coordinator")


def make_coordinator(module):
    async def executor(func, *args):
        return await asyncio.to_thread(func, *args)

    entry = SimpleNamespace(title="test lamp", data={
        "host": "192.0.2.1", "device_id": "test-device", "auth_code": "private-auth",
    })
    return module.HiLinkCoordinator(SimpleNamespace(async_add_executor_job=executor), entry)


def fake_client(module, coordinator, monkeypatch, *, fail=False, on_session=None):
    attempts = []

    class Client:
        def __init__(self, timeout):
            self.timeout = timeout

        def create_session(self):
            attempts.append(self.timeout)
            if on_session:
                on_session()
            if fail:
                raise module.HiLinkError("no response")

        def read_state(self):
            return module.LightState(True, 15, 2700)

    monkeypatch.setattr(coordinator, "_new_client", lambda timeout=3.5: Client(timeout))
    return attempts


def test_window_opens_before_waiting_for_lock_and_closes_on_success(module, monkeypatch):
    async def scenario():
        coordinator = make_coordinator(module)
        attempts = fake_client(module, coordinator, monkeypatch)
        async with coordinator._lock:
            task = asyncio.create_task(coordinator.async_force_refresh())
            await asyncio.sleep(0)
            assert coordinator._in_recovery()
            remaining = coordinator._recovery_until - time.monotonic()
            assert 29 < remaining <= 30
            assert not task.done()
        await task
        assert attempts == [1.0]
        assert not coordinator._in_recovery()
        assert coordinator._client.timeout == 3.5
        assert coordinator.published.brightness == 15

    asyncio.run(scenario())


def test_failed_refresh_keeps_window_and_short_poll_only_attempts_once(module, monkeypatch):
    async def scenario():
        coordinator = make_coordinator(module)
        attempts = fake_client(module, coordinator, monkeypatch, fail=True)
        await coordinator.async_force_refresh()
        assert coordinator._in_recovery()
        assert hasattr(coordinator, "error")
        with pytest.raises(module.UpdateFailed):
            await coordinator._async_update_data()
        assert attempts == [1.0, 1.0]
        assert coordinator._client is None
        assert coordinator._in_recovery()

    asyncio.run(scenario())


def test_expired_window_restores_two_normal_attempts(module, monkeypatch):
    coordinator = make_coordinator(module)
    coordinator._recovery_until = time.monotonic() - 1
    attempts = fake_client(module, coordinator, monkeypatch, fail=True)
    with pytest.raises(module.HiLinkError):
        coordinator._sync_read()
    assert attempts == [3.5, 3.5]


def test_inflight_poll_skips_second_long_attempt_when_recovery_arrives(module, monkeypatch):
    coordinator = make_coordinator(module)
    attempts = fake_client(
        module, coordinator, monkeypatch, fail=True, on_session=coordinator._start_recovery,
    )
    with pytest.raises(module.HiLinkError):
        coordinator._sync_read()
    assert attempts == [3.5]
    assert coordinator._in_recovery()


def test_poll_rechecks_window_after_lock_wait(module, monkeypatch):
    async def scenario():
        coordinator = make_coordinator(module)
        attempts = fake_client(module, coordinator, monkeypatch)
        async with coordinator._lock:
            task = asyncio.create_task(coordinator._async_update_data())
            await asyncio.sleep(0)
            coordinator._start_recovery()
        state = await task
        assert state.brightness == 15
        assert attempts == [1.0]
        assert not coordinator._in_recovery()
        assert coordinator._client.timeout == 3.5

    asyncio.run(scenario())


def test_repeated_refresh_extends_window_and_lamps_are_independent(module):
    first = make_coordinator(module)
    second = make_coordinator(module)
    first._recovery_until = time.monotonic() + 1
    previous = first._recovery_until
    first._start_recovery()
    assert first._recovery_until > previous + 28
    assert not second._in_recovery()


def test_control_keeps_normal_timeout_and_ends_recovery_on_success(module, monkeypatch):
    coordinator = make_coordinator(module)
    coordinator._start_recovery()
    attempts = fake_client(module, coordinator, monkeypatch)
    # Empty commands still exercise session creation and confirmed state read.
    asyncio.run(coordinator.async_send([]))
    assert attempts == [3.5]
    assert coordinator.published.brightness == 15
    assert not coordinator._in_recovery()
