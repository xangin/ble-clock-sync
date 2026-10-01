"""PVVX 0x23 notifications, verified against the official firmware parser."""

from __future__ import annotations

import asyncio
import logging
import struct
from datetime import datetime

from bleak import BleakClient, BleakGATTCharacteristic

from .base import (
    Now,
    ProtocolChanged,
    UnsupportedFormat,
    VerificationError,
    displayed_time,
    local_epoch,
)

_LOGGER = logging.getLogger(__name__)
SERVICE = "00001f10-0000-1000-8000-00805f9b34fb"
CHARACTERISTIC = "00001f1f-0000-1000-8000-00805f9b34fb"
COMMAND = 0x23
RESPONSE_TIMEOUT = 8


def encode_time(now: datetime) -> bytes:
    """PVVX displays its counter without applying a separate timezone field."""
    return struct.pack("<BI", COMMAND, int(local_epoch(now)))


def decode_time(value: bytes | bytearray, now: datetime) -> datetime:
    """The optional final uint32 is last-set time, not status or timezone."""
    if value == b"\x23\xff":
        raise ProtocolChanged("Unsupported command 0x23")
    if len(value) not in (5, 9) or value[0] != COMMAND:
        raise UnsupportedFormat("Invalid PVVX time response")
    return displayed_time(struct.unpack_from("<I", value, 1)[0], now)


class PVVXProtocol:
    """Clock command transactions on a connection already managed by HA."""

    id = "pvvx"
    firmware_family = "pvvx"

    def supports_model(self, model: str) -> bool:
        """GATT and the time command establish PVVX capability independently of model."""
        return True

    async def probe(self, client: BleakClient) -> bool:
        service = client.services.get_service(SERVICE)
        found = service is not None and service.get_characteristic(CHARACTERISTIC) is not None
        _LOGGER.debug(
            "PVVX service found=%s service=%s control characteristic=%s",
            found,
            SERVICE,
            CHARACTERISTIC,
        )
        return found

    async def _exchange(self, client: BleakClient, payload: bytes) -> bytes:
        future: asyncio.Future[bytes] = asyncio.get_running_loop().create_future()

        def received(_sender: BleakGATTCharacteristic, data: bytearray) -> None:
            # Never log unsolicited payloads: the shared control channel can carry keys.
            if data and data[0] == COMMAND and not future.done():
                _LOGGER.debug("PVVX time response received length=%d", len(data))
                future.set_result(bytes(data))

        await client.start_notify(CHARACTERISTIC, received)
        try:
            await client.write_gatt_char(CHARACTERISTIC, payload, response=False)
            _LOGGER.debug(
                "PVVX %s Time payload sent length=%d",
                "Get" if len(payload) == 1 else "Set",
                len(payload),
            )
            async with asyncio.timeout(RESPONSE_TIMEOUT):
                return await future
        finally:
            if client.is_connected:
                await client.stop_notify(CHARACTERISTIC)

    async def read_time(self, client: BleakClient, now: datetime) -> datetime:
        value = await self._exchange(client, bytes([COMMAND]))
        result = decode_time(value, now)
        _LOGGER.debug("PVVX current clock time=%s", result.isoformat())
        return result

    async def write_time(self, client: BleakClient, now: datetime) -> None:
        response = await self._exchange(client, encode_time(now))
        acknowledged = decode_time(response, now)
        # A source-backed echo, NOT a made-up ACK status byte.
        if abs(acknowledged.timestamp() - now.timestamp()) > 10:
            raise VerificationError("PVVX SET response did not acknowledge requested time")

    async def verify_time(self, client: BleakClient, now: Now) -> float:
        result = await self.read_time(client, now())
        error = result.timestamp() - now().timestamp()
        _LOGGER.debug("PVVX read-back final error=%+.2f s", error)
        if abs(error) > 10:
            raise VerificationError("PVVX read-back differs by more than 10 seconds")
        return error
