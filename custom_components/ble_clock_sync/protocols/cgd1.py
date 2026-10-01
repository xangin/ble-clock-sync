"""CGD1 clock-only protocol adapted from rjocoleman/qingping-cgd1 (MIT).

Source revision 855a955; no upstream scanner, client or automatic connection used.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timedelta

from bleak import BleakClient, BleakGATTCharacteristic
from bleak.exc import BleakError

from .base import (
    AuthenticationRequired,
    Now,
    UnsupportedFormat,
    VerificationError,
    cache_invalid,
    offset_seconds,
)

_LOGGER = logging.getLogger(__name__)
SERVICE = "22210000-554a-4546-5542-46534450464d"
ADVERTISEMENT_SERVICE = "0000fdcd-0000-1000-8000-00805f9b34fb"
AUTH_WRITE = "00000001-0000-1000-8000-00805f9b34fb"
AUTH_NOTIFY = "00000002-0000-1000-8000-00805f9b34fb"
DATA_WRITE = "0000000b-0000-1000-8000-00805f9b34fb"
DATA_NOTIFY = "0000000c-0000-1000-8000-00805f9b34fb"
READ_SETTINGS = b"\x01\x02"
COMMAND_TIMEOUT = 10
CONF_AUTH_TOKEN = "auth_token"


def validate_token(value: str) -> bytes:
    """Require a 16-byte CGD1 pairing secret without including it in errors."""
    try:
        token = bytes.fromhex(value)
    except ValueError:
        raise ValueError("Invalid CGD1 token") from None
    if len(token) != 16:
        raise ValueError("Invalid CGD1 token")
    return token


def validate_settings(data: bytes) -> bytes:
    if len(data) != 20 or data[:2] not in (b"\x13\x01", b"\x13\x02"):
        raise UnsupportedFormat("Invalid CGD1 settings response")
    return data


def timezone_settings(data: bytes, now: datetime) -> bytes:
    """Patch only source-verified timezone fields; preserve other settings exactly."""
    blob = bytearray(validate_settings(data))
    minutes = int(offset_seconds(now) / 60)
    blob[:2] = b"\x13\x01"
    blob[6] = abs(minutes) // 6
    blob[13] = int(minutes >= 0)
    return bytes(blob)


def encode_time(now: datetime) -> bytes:
    offset_seconds(now)  # Reject naive time.
    return b"\x05\x09" + int(now.timestamp()).to_bytes(4, "little")


class CGD1Protocol:
    id = "qingping_cgd1"
    firmware_family = "qingping_stock"

    def __init__(self, token: str | None = None) -> None:
        self._token = token
        self._settings: bytes | None = None
        self._expected_timezone: tuple[int, int] | None = None
        self._time_acked = False

    def supports_model(self, model: str) -> bool:
        return True

    async def probe(self, client: BleakClient) -> bool:
        service = client.services.get_service(SERVICE)
        found = service is not None and all(
            service.get_characteristic(uuid) is not None
            for uuid in (AUTH_WRITE, AUTH_NOTIFY, DATA_WRITE, DATA_NOTIFY)
        )
        _LOGGER.debug("CGD1 GATT probe service=%s found=%s", SERVICE, found)
        return found

    async def _exchange(
        self,
        client: BleakClient,
        writes: list[tuple[str, bytes]],
        accepts: Callable[[bytes], bool],
        *,
        authenticating: bool = False,
    ) -> bytes:
        future: asyncio.Future[bytes] = asyncio.get_running_loop().create_future()

        def received(_sender: BleakGATTCharacteristic, raw: bytearray) -> None:
            data = bytes(raw)
            # No raw auth/settings payload logging, including unrelated notifications.
            if not future.done() and accepts(data):
                future.set_result(data)

        subscribed: list[str] = []
        try:
            for char in (AUTH_NOTIFY, DATA_NOTIFY):
                await client.start_notify(char, received)
                subscribed.append(char)
            try:
                async with asyncio.timeout(COMMAND_TIMEOUT):
                    for char, payload in writes:
                        await client.write_gatt_char(char, payload, response=True)
                    return await future
            except (TimeoutError, BleakError) as error:
                if authenticating and not cache_invalid(error):
                    # Upstream confirms auth ACK alone is unreliable: privileged reads
                    # fail/drop on a wrong token. Do not expose backend payload strings.
                    raise AuthenticationRequired(
                        "CGD1 authentication could not be verified"
                    ) from None
                raise
        finally:
            for char in reversed(subscribed):
                if client.is_connected:
                    try:
                        await client.stop_notify(char)
                    except BleakError:
                        _LOGGER.debug("CGD1 notification cleanup failed; manager will disconnect")

    async def _read_settings(self, client: BleakClient) -> bytes:
        value = await self._exchange(
            client, [(DATA_WRITE, READ_SETTINGS)], lambda data: data[:1] == b"\x13"
        )
        return validate_settings(value)

    async def read_time(self, client: BleakClient, now: datetime) -> None:
        """Authenticate and prepare settings; no verified clock GET command exists."""
        try:
            token = validate_token(self._token or "")
        except ValueError:
            raise AuthenticationRequired("CGD1 authentication required") from None
        response = await self._exchange(
            client,
            [
                (AUTH_WRITE, b"\x11\x01" + token),
                (AUTH_WRITE, b"\x11\x02" + token),
                (DATA_WRITE, READ_SETTINGS),
            ],
            lambda data: data[:1] == b"\x13",
            authenticating=True,
        )
        self._settings = validate_settings(response)
        _LOGGER.debug("CGD1 authentication verified by privileged settings read")

    async def _write_ack(self, client: BleakClient, char: str, payload: bytes) -> None:
        subcommand = payload[1]
        ack = await self._exchange(
            client,
            [(char, payload)],
            lambda data: len(data) >= 5 and data[:2] == b"\x04\xff" and data[2] == subcommand,
        )
        if ack[4] not in (0x00, 0x09):
            raise VerificationError("CGD1 command rejected")
        _LOGGER.debug("CGD1 command ACK verified subcommand=0x%02x", subcommand)

    async def write_time(self, client: BleakClient, now: datetime) -> None:
        self._time_acked = False
        if self._settings is None:
            raise AuthenticationRequired("CGD1 authentication required")
        started = asyncio.get_running_loop().time()
        desired = timezone_settings(self._settings, now)
        self._expected_timezone = (desired[6], desired[13])
        if (self._settings[6], self._settings[13]) != self._expected_timezone:
            await self._write_ack(client, DATA_WRITE, desired)
        if offset_seconds(now) % 360:
            _LOGGER.warning("CGD1 timezone is quantized to six-minute increments by its firmware")
        # Account for settings-write latency without using the process timezone.
        current = now + timedelta(seconds=asyncio.get_running_loop().time() - started)
        await self._write_ack(client, AUTH_WRITE, encode_time(current))
        self._time_acked = True

    async def verify_time(self, client: BleakClient, now: Now) -> None:
        """Time uses its command ACK; timezone is verified with a fresh settings read."""
        if not self._time_acked:
            raise VerificationError("CGD1 time command has no verified ACK")
        settings = await self._read_settings(client)
        if (settings[6], settings[13]) != self._expected_timezone:
            raise VerificationError("CGD1 timezone read-back mismatch")
        _LOGGER.debug("CGD1 time ACK and timezone read-back verified")
