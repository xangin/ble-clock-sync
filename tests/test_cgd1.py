"""Source-derived CGD1 frames and integration behavior without BLE hardware."""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from bleak.exc import BleakError
from homeassistant.config_entries import SOURCE_BLUETOOTH
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ble_clock_sync.const import DOMAIN
from custom_components.ble_clock_sync.diagnostics import async_get_config_entry_diagnostics
from custom_components.ble_clock_sync.discovery import candidate_reason, model_from_info
from custom_components.ble_clock_sync.protocols import detect_protocol
from custom_components.ble_clock_sync.protocols.base import (
    AuthenticationRequired,
    UnsupportedFormat,
    VerificationError,
)
from custom_components.ble_clock_sync.protocols.cgd1 import (
    ADVERTISEMENT_SERVICE,
    AUTH_NOTIFY,
    AUTH_WRITE,
    DATA_NOTIFY,
    DATA_WRITE,
    READ_SETTINGS,
    SERVICE,
    CGD1Protocol,
    encode_time,
    timezone_settings,
    validate_token,
)

from .conftest import ADDRESS, FakeClock, service_info
from .test_manager import settle

TOKEN = "0123456789abcdef0123456789abcdef"
# Non-default flags/reserved fields deliberately retained in a timezone-only write.
SETTINGS = bytes.fromhex("130204a1b2870005451600061e00ccdd12345678")


class CGD1Services:
    def __init__(self):
        self.uuids = {AUTH_WRITE, AUTH_NOTIFY, DATA_WRITE, DATA_NOTIFY}

    def get_service(self, uuid):
        return self if uuid == SERVICE else None

    def get_characteristic(self, uuid):
        return (
            SimpleNamespace(uuid=uuid, properties=["write", "notify"])
            if uuid in self.uuids
            else None
        )

    def __iter__(self):
        return iter([SimpleNamespace(uuid=SERVICE)])


class FakeCGD1(FakeClock):
    def __init__(self):
        super().__init__()
        self.services = CGD1Services()
        self.settings = SETTINGS
        self.notifications = {}
        self.records = []
        self.authorized = True
        self.read_error = False
        self.ack_status = 0
        self.wrong_ack = False
        self.ignore_settings = False
        self.fail_notify = None
        self.reads = 0

    async def start_notify(self, uuid, callback):
        if uuid == self.fail_notify:
            raise BleakError("notify failed")
        self.notifications[uuid] = callback

    async def stop_notify(self, uuid):
        self.notifications.pop(uuid, None)
        self.stop_count += 1

    async def write_gatt_char(self, uuid, data, response):
        assert response is True
        self.records.append((uuid, bytes(data)))
        self.writes.append(bytes(data))
        if data[:2] in (b"\x11\x01", b"\x11\x02"):
            assert uuid == AUTH_WRITE
            assert len(data) == 18
            # Even a bad token receives this ACK. It must NOT count as auth proof.
            self._notify(AUTH_NOTIFY, b"\x04\xff\x01\x00\x00")
        elif data == READ_SETTINGS:
            assert uuid == DATA_WRITE
            self.reads += 1
            if self.read_error:
                raise BleakError("privileged read disconnected")
            if self.authorized:
                self._notify(DATA_NOTIFY, self.settings)
        elif data[:2] == b"\x13\x01":
            assert uuid == DATA_WRITE
            if not self.ignore_settings:
                self.settings = b"\x13\x02" + data[2:]
            self._notify(DATA_NOTIFY, b"\x04\xff\x01\x00" + bytes([self.ack_status]))
        elif data[:2] == b"\x05\x09":
            assert uuid == AUTH_WRITE
            command = 0x08 if self.wrong_ack else 0x09
            self._notify(AUTH_NOTIFY, bytes([4, 255, command, 0, self.ack_status]))
        else:
            raise AssertionError("Unexpected non-clock command")

    def _notify(self, char, data):
        if callback := self.notifications.get(char):
            callback(None, bytearray(data))


@pytest.mark.parametrize(
    ("zone", "magnitude", "sign"),
    [
        ("Asia/Taipei", 80, 1),
        ("America/New_York", 50, 0),
        ("Asia/Kolkata", 55, 1),
        ("America/St_Johns", 35, 0),
        ("UTC", 0, 1),
        ("Asia/Kathmandu", 57, 1),
    ],
)
def test_timezone_preserves_all_other_settings(zone, magnitude, sign):
    now = datetime(2026, 1, 15, 12, tzinfo=ZoneInfo(zone))
    result = timezone_settings(SETTINGS, now)
    assert result[6] == magnitude and result[13] == sign
    assert result[:2] == b"\x13\x01"
    assert all(result[i] == SETTINGS[i] for i in range(20) if i not in (1, 6, 13))
    assert encode_time(now) == b"\x05\x09" + int(now.timestamp()).to_bytes(4, "little")


def test_dst_timezone_and_source_time_vector():
    summer = datetime(2026, 7, 1, tzinfo=ZoneInfo("Europe/Paris"))
    winter = datetime(2026, 12, 1, tzinfo=ZoneInfo("Europe/Paris"))
    assert timezone_settings(SETTINGS, summer)[6] == 20
    assert timezone_settings(SETTINGS, winter)[6] == 10
    assert encode_time(datetime(2026, 7, 1, tzinfo=UTC)) == bytes.fromhex("05098058446a")


@pytest.mark.parametrize("token", ["", "xyz", "00", "ff" * 17])
def test_invalid_token_is_redacted(token):
    with pytest.raises(ValueError, match="Invalid CGD1 token"):
        validate_token(token)


@pytest.mark.parametrize("settings", [b"", bytes(19), bytes(20), bytes(21)])
def test_malformed_settings(settings):
    with pytest.raises(UnsupportedFormat):
        timezone_settings(settings, datetime.now(UTC))


@pytest.mark.parametrize("ack_status", [0, 9])
async def test_auth_sync_ack_timezone_readback(ack_status, caplog):
    clock = FakeCGD1()
    clock.ack_status = ack_status
    handler = await detect_protocol(clock, token=TOKEN)
    now = datetime(2026, 10, 1, 12, tzinfo=ZoneInfo("Asia/Taipei"))
    caplog.set_level("DEBUG")
    assert handler.id == "qingping_cgd1"
    assert await handler.read_time(clock, now) is None
    await handler.write_time(clock, now)
    assert await handler.verify_time(clock, lambda: now) is None
    assert clock.records[:3] == [
        (AUTH_WRITE, b"\x11\x01" + bytes.fromhex(TOKEN)),
        (AUTH_WRITE, b"\x11\x02" + bytes.fromhex(TOKEN)),
        (DATA_WRITE, READ_SETTINGS),
    ]
    assert clock.reads == 2
    assert not clock.notifications
    assert TOKEN not in caplog.text
    assert "1234567890" not in caplog.text


@pytest.mark.parametrize("mode", ["no_token", "wrong_token_ack", "privileged_disconnect"])
async def test_auth_requires_privileged_read(mode):
    clock = FakeCGD1()
    handler = CGD1Protocol(None if mode == "no_token" else TOKEN)
    clock.authorized = False
    clock.read_error = mode == "privileged_disconnect"
    with patch("custom_components.ble_clock_sync.protocols.cgd1.COMMAND_TIMEOUT", 0.01):
        with pytest.raises(AuthenticationRequired):
            await handler.read_time(clock, datetime.now(UTC))
    assert not clock.notifications
    assert not any(data[:2] == b"\x05\x09" for data in clock.writes)


@pytest.mark.parametrize("failure", ["rejected_ack", "wrong_ack", "timezone_mismatch"])
async def test_failed_verification(failure):
    clock = FakeCGD1()
    handler = CGD1Protocol(TOKEN)
    now = datetime(2026, 10, 1, tzinfo=ZoneInfo("Asia/Taipei"))
    await handler.read_time(clock, now)
    clock.ack_status = 4 if failure == "rejected_ack" else 0
    clock.wrong_ack = failure == "wrong_ack"
    clock.ignore_settings = failure == "timezone_mismatch"
    with patch("custom_components.ble_clock_sync.protocols.cgd1.COMMAND_TIMEOUT", 0.01):
        with pytest.raises((VerificationError, TimeoutError)):
            await handler.write_time(clock, now)
            await handler.verify_time(clock, lambda: now)
    assert not clock.notifications


async def test_cancel_and_partial_subscription_cleanup():
    clock = FakeCGD1()
    clock.authorized = False
    task = asyncio.create_task(CGD1Protocol(TOKEN).read_time(clock, datetime.now(UTC)))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not clock.notifications
    clock.fail_notify = DATA_NOTIFY
    with pytest.raises((AuthenticationRequired, BleakError)):
        await CGD1Protocol(TOKEN).read_time(clock, datetime.now(UTC))
    assert not clock.notifications


def test_discovery_filters_other_qingping_models():
    info = service_info(name="Renamed")
    info.service_data[ADVERTISEMENT_SERVICE] = b"\x08\x0c" + bytes(15)
    assert candidate_reason(info) == "cgd1_name_service_or_model"
    assert model_from_info(info) == "CGD1"
    info.service_data[ADVERTISEMENT_SERVICE] = b"\x08\x09" + bytes(15)
    assert candidate_reason(info) is None
    info.service_data[ADVERTISEMENT_SERVICE] = b"\x08\x0c"
    assert candidate_reason(info) is None


async def cgd1_entry(hass, ble, token=TOKEN):
    ble.clock = FakeCGD1()
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=ADDRESS,
        title="CGD1",
        data={
            "address": ADDRESS,
            "model": "CGD1",
            "auth_token": token,
            "credential_revision": 1,
        },
        options={"schedule": "manual", "model": "CGD1"},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await settle(hass)
    return entry


@pytest.mark.parametrize("source", ["local", "esphome-proxy-123"])
async def test_cgd1_ha_transport_and_no_secret_storage(hass, ble, hass_storage, source):
    ble.source = source
    entry = await cgd1_entry(hass, ble)
    assert entry.runtime_data.status == "synced"
    assert entry.runtime_data.protocol == "qingping_cgd1"
    assert ble.devices[0].details["source"] == source
    assert ble.clock.disconnects == 1
    assert TOKEN not in str(await async_get_config_entry_diagnostics(hass, entry))
    assert TOKEN not in str(hass_storage[f"{DOMAIN}.{entry.entry_id}"]["data"])
    assert not any("drift" in state.entity_id for state in hass.states.async_all())
    assert (
        hass.states.get(hass.states.async_entity_ids("button")[0]).attributes["icon"]
        == "mdi:clock-sync"
    )


async def test_options_token_recovery_preserves_schedule(hass, ble):
    entry = await cgd1_entry(hass, ble, token="invalid")
    assert entry.runtime_data.status == "authentication_required"
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert "invalid" not in str(result["data_schema"])
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"schedule": "daily", "change_auth_token": True}
    )
    assert result["step_id"] == "cgd1_auth"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"auth_token": "bad"}
    )
    assert result["errors"] == {"auth_token": "invalid_token"}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"auth_token": TOKEN}
    )
    await settle(hass)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["schedule"] == "manual"
    assert entry.data["credential_revision"] == 2
    assert entry.runtime_data.status == "synced"
    assert len(ble._callbacks) == 1


async def test_new_pairing_requires_explicit_confirmation_and_random_token(hass, ble):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=service_info(name="CGD1")
    )
    assert ble.connections == 0
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["step_id"] == "cgd1_auth"
    assert ble.connections == 0
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"auth_token": "bad"}
    )
    assert result["errors"] == {"auth_token": "invalid_token"}
    with patch("custom_components.ble_clock_sync.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert len(bytes.fromhex(result["data"]["auth_token"])) == 16
    assert result["data"]["auth_token"] != TOKEN
    assert result["data"]["model"] == "CGD1"


async def test_drift_upgrade_removes_only_owned_entity(hass, ble, hass_storage):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=ADDRESS,
        title="Clock",
        data={"address": ADDRESS, "model": "LYWSD02"},
        options={"schedule": "manual"},
    )
    other = MockConfigEntry(
        domain="other_clock", unique_id="AA:BB:CC:DD:EE:FF", data={"address": "AA:BB:CC:DD:EE:FF"}
    )
    entry.add_to_hass(hass)
    other.add_to_hass(hass)
    registry = er.async_get(hass)
    old = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{ADDRESS}_clock_drift",
        config_entry=entry,
        suggested_object_id="renamed_old_drift",
    )
    unrelated = registry.async_get_or_create(
        "sensor", DOMAIN, "AA:BB:CC:DD:EE:FF_clock_drift", config_entry=other
    )
    key = f"{DOMAIN}.{entry.entry_id}"
    hass_storage[key] = {"version": 1, "minor_version": 1, "key": key, "data": {"drift": 17.5}}
    assert await hass.config_entries.async_setup(entry.entry_id)
    await settle(hass)
    assert registry.async_get(old.entity_id) is None
    assert registry.async_get(unrelated.entity_id) is not None
    assert "drift" not in hass_storage[key]["data"]
