"""HA integration lifecycle, transport routing and recovery behavior."""

import asyncio
import struct
from datetime import timedelta
from unittest.mock import patch

import pytest
from bleak.exc import BleakError
from homeassistant.const import CONF_ADDRESS
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.ble_clock_sync.const import DEFAULT_OPTIONS, DOMAIN
from custom_components.ble_clock_sync.diagnostics import async_get_config_entry_diagnostics
from custom_components.ble_clock_sync.manager import ClockSyncManager

from .conftest import ADDRESS, TITLE, FakeClock


async def settle(hass):
    await hass.async_block_till_done(wait_background_tasks=True)


async def setup_clock(hass, options=None, address=ADDRESS, model="LYWSD02"):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=address,
        title=TITLE,
        data={CONF_ADDRESS: address, "model": model},
        options=options or DEFAULT_OPTIONS,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await settle(hass)
    return entry


@pytest.mark.parametrize("source", ["local", "esphome-proxy-123"])
@pytest.mark.parametrize("protocol", ["pvvx", "xiaomi_stock"])
async def test_setup_sync_and_entities(hass, freezer, ble, source, protocol):
    freezer.move_to("2026-10-01T04:00:00+00:00")
    await hass.config.async_set_time_zone("Asia/Taipei")
    ble.source = source
    ble.clock = FakeClock(protocol)
    now = int(dt_util.utcnow().timestamp())
    ble.clock.value = (
        struct.pack("<I", now + 28800 + 17)
        if protocol == "pvvx"
        else struct.pack("<Ib", now + 17, 8)
    )
    entry = await setup_clock(hass, model="MJWSD05MMC" if protocol == "pvvx" else "LYWSD02")
    manager = entry.runtime_data
    assert manager.status == "synced"
    assert manager.protocol == protocol
    assert manager.connection_source == source
    assert ble.devices[0].details == {"source": source}
    assert ble.clock.disconnects == 1
    states = hass.states.async_all()
    assert len(states) == 7
    assert len([s for s in states if s.domain == "button"]) == 1
    assert not any("temperature" in s.entity_id or "humidity" in s.entity_id for s in states)
    assert manager.next_sync.hour == 4


async def test_offline_wait_and_manual_error(hass, ble):
    ble.in_range = False
    entry = await setup_clock(hass)
    manager = entry.runtime_data
    assert manager.status == "waiting"
    assert ble.connections == 0
    with pytest.raises(HomeAssistantError) as error:
        await manager.async_sync()
    assert error.value.translation_key == "not_in_range"
    ble.in_range = True
    ble.advertise()
    await settle(hass)
    assert manager.status == "synced"
    assert ble.connections == 1
    ble.advertise()
    await settle(hass)
    assert ble.connections == 1


async def test_passive_proxy_distinct_from_offline(hass, ble):
    ble.passive_only = True
    entry = await setup_clock(hass)
    assert entry.runtime_data.last_result == "no_active_connection"
    assert entry.runtime_data.status == "waiting"
    assert ble.connections == 0
    ble.passive_only = False
    ble.advertise()
    await settle(hass)
    assert entry.runtime_data.status == "synced"


async def test_backoff_all_steps_no_advertisement_storm(hass, freezer, ble):
    ble.connect_error = BleakError("No free connection slot")
    entry = await setup_clock(hass)
    manager = entry.runtime_data
    for minutes in [5, 15, 30, 60, 60]:
        assert manager._retry_at - dt_util.utcnow() == timedelta(minutes=minutes)
        connections = ble.connections
        for _ in range(10):
            ble.advertise()
        await settle(hass)
        assert ble.connections == connections
        freezer.tick(timedelta(minutes=minutes))
        async_fire_time_changed(hass)
        await settle(hass)
        assert ble.connections == connections + 1
    ble.connect_error = None
    await manager.async_sync()
    assert manager.status == "synced"
    assert manager._retry_at is None


@pytest.mark.parametrize("at_connect", [False, True])
async def test_authentication_stops_automatic_retries(hass, freezer, ble, at_connect):
    ble.clock = FakeClock("pvvx")
    error = BleakError("Insufficient Authentication")
    if at_connect:
        ble.connect_error = error
    else:
        ble.clock.error = error
    entry = await setup_clock(hass, model="MJWSD05MMC")
    manager = entry.runtime_data
    assert manager.status == "authentication_required"
    if not at_connect:
        assert manager.protocol == "pvvx"
    count = ble.connections
    freezer.tick(timedelta(days=1))
    async_fire_time_changed(hass)
    ble.advertise()
    await settle(hass)
    assert ble.connections == count
    ble.connect_error = ble.clock.error = None
    await manager.async_sync()
    assert manager.status == "synced"


async def test_schedule_dst_timezone_changes(hass, freezer, ble):
    await hass.config.async_set_time_zone("Europe/Paris")
    freezer.move_to("2026-10-24T22:00:00+00:00")
    entry = await setup_clock(hass)
    manager = entry.runtime_data
    assert struct.unpack("<Ib", ble.clock.writes[-1])[1] == 2
    freezer.move_to("2026-10-25T01:02:00+00:00")
    async_fire_time_changed(hass)
    await settle(hass)
    assert struct.unpack("<Ib", ble.clock.writes[-1])[1] == 1
    freezer.move_to("2026-10-25T03:00:00+00:00")
    async_fire_time_changed(hass)
    await settle(hass)
    assert len(ble.clock.writes) == 3
    await hass.config.async_set_time_zone("Asia/Kolkata")
    manager._async_check_offset(dt_util.now())
    await settle(hass)
    assert struct.unpack("<Ib", ble.clock.writes[-1])[1] == 5
    assert manager.next_sync.utcoffset() == timedelta(hours=5, minutes=30)


async def test_manual_button_and_request_coalescing(hass, ble):
    entry = await setup_clock(hass, {"schedule": "manual"})
    assert entry.runtime_data.next_sync is None
    buttons = hass.states.async_entity_ids("button")
    await hass.services.async_call("button", "press", {"entity_id": buttons[0]}, blocking=True)
    assert len(ble.clock.writes) == 2
    await asyncio.gather(*(entry.runtime_data.async_sync() for _ in range(8)))
    assert len(ble.clock.writes) == 3


async def test_cache_firmware_change_reconnect_same_entry(hass, ble):
    entry = await setup_clock(hass)
    manager = entry.runtime_data
    assert manager.protocol == "xiaomi_stock"
    replacement = FakeClock("pvvx")
    ble.clock = replacement
    await manager.async_sync()
    assert manager.protocol == "pvvx"
    assert manager.model == "LYWSD02"
    assert ble.connections == 3  # original, invalidated connection, re-probe
    assert replacement.clear_count == 1
    assert replacement.disconnects == 2
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1


async def test_unknown_command_reconnects_only_once(hass, ble):
    ble.clock = FakeClock("pvvx")
    ble.clock.unsupported = True
    entry = await setup_clock(hass)
    assert ble.connections == 2
    assert entry.runtime_data.last_result == "unsupported_protocol"
    assert entry.runtime_data.protocol is None
    assert ble.clock.clear_count == 2


async def test_six_byte_stock_never_written(hass, ble):
    ble.clock.value = bytes(6)
    entry = await setup_clock(hass)
    assert entry.runtime_data.last_result == "unsupported_protocol"
    assert ble.clock.writes == []


async def test_mjwsd_stock_excluded(hass, ble):
    entry = await setup_clock(hass, model="MJWSD05MMC")
    assert entry.runtime_data.last_result == "unsupported_protocol"
    assert not ble.clock.writes


async def test_restore_success_and_pending_backoff(hass, freezer, ble):
    freezer.move_to("2026-10-01T12:00:00+00:00")
    entry = await setup_clock(hass, {"schedule": "manual"})
    manager = entry.runtime_data
    await manager.async_stop()
    restored = ClockSyncManager(hass, entry, ADDRESS)
    await restored.async_start()
    await settle(hass)
    assert restored.last_sync == manager.last_sync
    assert restored.protocol == "xiaomi_stock"
    assert ble.connections == 1
    ble.connect_error = BleakError("timeout")
    with pytest.raises(HomeAssistantError):
        await restored.async_sync()
    retry_at = restored._retry_at
    await restored.async_stop()
    again = ClockSyncManager(hass, entry, ADDRESS)
    await again.async_start()
    await settle(hass)
    assert again._retry_at == retry_at
    assert ble.connections == 2
    ble.connect_error = None
    freezer.tick(timedelta(minutes=5))
    async_fire_time_changed(hass)
    await settle(hass)
    assert again.status == "synced"
    assert ble.connections == 3
    await again.async_stop()


async def test_unload_cancels_pending_connection(hass, ble):
    entry = await setup_clock(hass)
    ble.clock = FakeClock("pvvx")
    ble.clock.silent = True
    entry.runtime_data.protocol = None
    entry.runtime_data._start_background()
    for _ in range(10):
        await asyncio.sleep(0)
        if ble.clock.notify:
            break
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert ble.clock.disconnects == 1
    assert ble.clock.notify is None
    assert not ble._callbacks


async def test_global_lock_two_devices(hass, ble):
    one = await setup_clock(hass)
    two = await setup_clock(hass, address="AA:BB:CC:DD:EE:FF")
    active = 0
    maximum = 0
    original = ble.connect

    async def connect(*args, **kwargs):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0)
        return await original(*args, **kwargs)

    original_disconnect = ble.clock.disconnect

    async def disconnect():
        nonlocal active
        await original_disconnect()
        active -= 1

    with (
        patch("custom_components.ble_clock_sync.manager.establish_connection", side_effect=connect),
        patch.object(ble.clock, "disconnect", side_effect=disconnect),
    ):
        await asyncio.gather(one.runtime_data.async_sync(), two.runtime_data.async_sync())
    assert maximum == 1
    assert active == 0


async def test_diagnostics_redact_and_remove_history(hass, ble, hass_storage):
    entry = await setup_clock(hass)
    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    assert ADDRESS not in str(diagnostics)
    assert TITLE not in str(diagnostics)
    assert diagnostics["hardware_validation"] == "not_verified"
    key = f"{DOMAIN}.{entry.entry_id}"
    assert key in hass_storage
    assert await hass.config_entries.async_remove(entry.entry_id)
    await settle(hass)
    assert key not in hass_storage


async def test_automatic_minimum_interval(hass, freezer, ble):
    entry = await setup_clock(hass, {"schedule": "manual"})
    entry.runtime_data.async_request_sync()
    ble.advertise()
    await settle(hass)
    assert ble.connections == 1
    freezer.tick(timedelta(hours=1))
    async_fire_time_changed(hass)
    await settle(hass)
    assert ble.connections == 2


@pytest.mark.parametrize("missed", [True, False])
async def test_restart_catches_missed_run_and_offline_timezone(hass, freezer, ble, missed):
    await hass.config.async_set_time_zone("Asia/Taipei")
    freezer.move_to("2026-10-01T12:00:00+00:00")
    entry = await setup_clock(hass)
    await entry.runtime_data.async_stop()
    if missed:
        freezer.tick(timedelta(days=1))
    else:
        await hass.config.async_set_time_zone("Asia/Kolkata")
    restored = ClockSyncManager(hass, entry, ADDRESS)
    await restored.async_start()
    await settle(hass)
    assert ble.connections == 2
    assert restored.status == "synced"
    assert restored._synced_timezone == hass.config.time_zone
    await restored.async_stop()


async def test_options_reload_without_extra_write(hass, ble):
    entry = await setup_clock(hass)
    hass.config_entries.async_update_entry(
        entry, options={"schedule": "manual", "model": "LYWSD02MMC"}
    )
    await settle(hass)
    assert entry.runtime_data.next_sync is None
    assert entry.runtime_data.model == "LYWSD02MMC"
    assert ble.connections == 1
    assert len(ble._callbacks) == 1


async def test_characteristic_disappears_reprobes(hass, ble):
    from bleak.exc import BleakCharacteristicNotFoundError

    entry = await setup_clock(hass)
    broken = FakeClock()
    broken.error = BleakCharacteristicNotFoundError("removed-by-new-firmware")
    changed = FakeClock("pvvx")
    ble.replacements = [broken, changed]
    await entry.runtime_data.async_sync()
    assert entry.runtime_data.protocol == "pvvx"
    assert broken.clear_count == 1
    assert broken.disconnects == 1
    assert changed.disconnects == 1
