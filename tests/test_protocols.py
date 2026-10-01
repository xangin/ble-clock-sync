"""Payload correctness, transaction handling and security regression tests."""

import asyncio
import struct
from datetime import UTC, datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from bleak.exc import BleakError

from custom_components.ble_clock_sync.protocols import detect_protocol
from custom_components.ble_clock_sync.protocols.base import (
    ProtocolChanged,
    UnsupportedFormat,
    UnsupportedProtocol,
    VerificationError,
    authentication_required,
    cache_invalid,
)
from custom_components.ble_clock_sync.protocols.pvvx import PVVXProtocol, decode_time, encode_time
from custom_components.ble_clock_sync.protocols.xiaomi_stock import XiaomiStockProtocol, XiaomiTime5

from .conftest import FakeClock


@pytest.mark.parametrize(
    ("zone", "hours", "remainder"),
    [
        ("Asia/Taipei", 8, 0),
        ("America/New_York", -5, 0),
        ("Asia/Kolkata", 5, 1800),
        ("Asia/Kathmandu", 5, 2700),
        ("America/St_Johns", -4, 1800),
        ("UTC", 0, 0),
    ],
)
def test_xiaomi_timezone(zone, hours, remainder):
    now = datetime(2026, 1, 15, 12, 0, 0, tzinfo=ZoneInfo(zone))
    payload = XiaomiTime5.encode(now)
    assert struct.unpack("<Ib", payload) == (int(now.timestamp()) + remainder, hours)
    assert XiaomiTime5.decode(payload, now).timestamp() == now.timestamp()


@pytest.mark.parametrize(
    "zone", ["Asia/Taipei", "America/New_York", "Asia/Kolkata", "America/St_Johns"]
)
@pytest.mark.parametrize("length", [5, 9])
def test_pvvx_local_epoch(zone, length):
    now = datetime(2026, 1, 15, 12, tzinfo=ZoneInfo(zone))
    payload = encode_time(now)
    assert struct.unpack("<BI", payload) == (
        0x23,
        int(now.timestamp() + now.utcoffset().total_seconds()),
    )
    if length == 9:
        payload += struct.pack("<I", 123)  # Last-set field must not change decoded time.
    assert decode_time(payload, now).timestamp() == now.timestamp()


@pytest.mark.parametrize("codec", ["xiaomi", "pvvx"])
def test_dst_transition(codec):
    zone = ZoneInfo("Europe/Paris")
    encode, decode = (
        (XiaomiTime5.encode, XiaomiTime5.decode)
        if codec == "xiaomi"
        else (encode_time, decode_time)
    )
    before = datetime(2026, 10, 25, 0, 59, tzinfo=UTC).astimezone(zone)
    after = datetime(2026, 10, 25, 1, 1, tzinfo=UTC).astimezone(zone)
    assert decode(encode(before), after).timestamp() - after.timestamp() == 3480
    assert decode(encode(after), after).timestamp() == after.timestamp()


@pytest.mark.parametrize("value", [b"", bytes(4), bytes(6), bytes(7), b"\x00\x00\x00\x00\x7f"])
def test_xiaomi_invalid_never_guessed(value):
    with pytest.raises(UnsupportedFormat):
        XiaomiTime5.decode(value, datetime.now(UTC))


@pytest.mark.parametrize(
    "value", [b"", b"\x23", bytes(5), b"\x23" + bytes(5), b"\x23" + bytes(7), b"\x23" + bytes(9)]
)
def test_pvvx_invalid(value):
    with pytest.raises(UnsupportedFormat):
        decode_time(value, datetime.now(UTC))


@pytest.mark.parametrize(
    "error",
    [
        "Insufficient Authentication",
        "Insufficient Encryption",
        "Insufficient Authorization",
        "Authorization Required",
        "org.bluez.Error.NotAuthorized",
        "ATT error: 0x05",
        "ATT error: 0x08",
        "ATT error: 0x0f",
    ],
)
def test_security_classification(error):
    assert authentication_required(BleakError(error))
    assert not cache_invalid(BleakError(error))


def test_advertisement_encryption_is_not_gatt_security():
    assert not authentication_required(BleakError("encrypted BTHome advertisement"))
    assert not authentication_required(BleakError("connection timeout"))


@pytest.mark.parametrize("protocol", ["pvvx", "xiaomi_stock"])
async def test_gatt_detection(protocol):
    clock = FakeClock(protocol)
    handler = await detect_protocol(clock)
    assert handler.id == protocol
    clock.services.service_uuid = "unrelated"
    with pytest.raises(UnsupportedProtocol):
        await detect_protocol(clock)


@pytest.mark.parametrize("length", [5, 9])
async def test_pvvx_get_set_echo_readback(length, caplog):
    clock = FakeClock("pvvx")
    clock.response_length = length
    clock.unsolicited = True
    handler = PVVXProtocol()
    now = datetime(2026, 10, 1, 12, tzinfo=ZoneInfo("Asia/Taipei"))
    clock.value = struct.pack("<I", int(now.timestamp()) + 28800 + 17)
    caplog.set_level("DEBUG")
    assert (await handler.read_time(clock, now)).timestamp() - now.timestamp() == 17
    await handler.write_time(clock, now)
    assert await handler.verify_time(clock, lambda: now) == 0
    assert clock.writes == [b"\x23", encode_time(now), b"\x23"]
    assert clock.stop_count == 3
    assert clock.notify is None
    assert "secret_bindkey" not in caplog.text


async def test_pvvx_unsupported_command():
    clock = FakeClock("pvvx")
    clock.unsupported = True
    with pytest.raises(ProtocolChanged):
        await PVVXProtocol().read_time(clock, datetime.now(UTC))
    assert clock.stop_count == 1


async def test_pvvx_timeout_and_cancellation_cleanup():
    clock = FakeClock("pvvx")
    clock.silent = True
    with patch("custom_components.ble_clock_sync.protocols.pvvx.RESPONSE_TIMEOUT", 0.01):
        with pytest.raises(TimeoutError):
            await PVVXProtocol().read_time(clock, datetime.now(UTC))
    task = asyncio.create_task(PVVXProtocol().read_time(clock, datetime.now(UTC)))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert clock.stop_count == 2
    assert clock.notify is None


@pytest.mark.parametrize("stage", ["get", "set", "verify"])
async def test_pvvx_authentication_propagates(stage):
    clock = FakeClock("pvvx")
    clock.error = BleakError("Insufficient Authentication")
    handler = PVVXProtocol()
    now = datetime.now(UTC)
    with pytest.raises(BleakError, match="Authentication"):
        if stage == "get":
            await handler.read_time(clock, now)
        elif stage == "set":
            await handler.write_time(clock, now)
        else:
            await handler.verify_time(clock, lambda: now)


@pytest.mark.parametrize("protocol", ["pvvx", "xiaomi_stock"])
async def test_failed_verification(protocol):
    clock = FakeClock(protocol)
    handler = await detect_protocol(clock)
    now = datetime.now(UTC)
    await handler.read_time(clock, now)
    clock.corrupt = True
    with pytest.raises(VerificationError):
        await handler.write_time(clock, now)
        await handler.verify_time(clock, lambda: now)


async def test_xiaomi_read_required_and_six_byte_blocked():
    clock = FakeClock()
    handler = XiaomiStockProtocol()
    with pytest.raises(UnsupportedFormat):
        await handler.write_time(clock, datetime.now(UTC))
    clock.value = bytes(6)
    with pytest.raises(UnsupportedFormat):
        await handler.read_time(clock, datetime.now(UTC))
    assert not clock.writes
