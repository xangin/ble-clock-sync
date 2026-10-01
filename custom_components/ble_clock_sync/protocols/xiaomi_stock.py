"""Source-backed five-byte Xiaomi time protocol; unknown lengths are blocked."""

from __future__ import annotations

import logging
import struct
from datetime import datetime

from bleak import BleakClient

from .base import Now, UnsupportedFormat, VerificationError, displayed_time, offset_seconds

_LOGGER = logging.getLogger(__name__)
SERVICE = "ebe0ccb0-7a0a-4b0c-8a1a-6ff2997da3a6"
CHARACTERISTIC = "ebe0ccb7-7a0a-4b0c-8a1a-6ff2997da3a6"


class XiaomiTime5:
    """Whole hours in int8, remaining offset seconds in the timestamp."""

    @staticmethod
    def encode(now: datetime) -> bytes:
        offset = offset_seconds(now)
        hours, remainder = divmod(offset, 3600)
        return struct.pack("<Ib", int(now.timestamp()) + remainder, hours)

    @staticmethod
    def decode(value: bytes | bytearray, now: datetime) -> datetime:
        if len(value) != 5:
            raise UnsupportedFormat(f"Unverified Xiaomi time length: {len(value)}")
        timestamp, hours = struct.unpack("<Ib", value)
        if not -12 <= hours <= 14:
            raise UnsupportedFormat("Invalid Xiaomi timezone byte")
        return displayed_time(timestamp + hours * 3600, now)


class XiaomiStockProtocol:
    """Select format by reading; do not guess a six-byte codec or fallback."""

    id = "xiaomi_stock"
    firmware_family = "xiaomi_stock"

    def __init__(self) -> None:
        self._validated = False

    def supports_model(self, model: str) -> bool:
        """Do not enable stock firmware explicitly deferred from v1 scope."""
        return model not in {"MJWSD05MMC"}

    async def probe(self, client: BleakClient) -> bool:
        service = client.services.get_service(SERVICE)
        found = service is not None and service.get_characteristic(CHARACTERISTIC) is not None
        _LOGGER.debug(
            "Xiaomi probe service=%s characteristic=%s found=%s", SERVICE, CHARACTERISTIC, found
        )
        return found

    async def read_time(self, client: BleakClient, now: datetime) -> datetime:
        value = await client.read_gatt_char(CHARACTERISTIC)
        _LOGGER.debug("Xiaomi time payload length=%d", len(value))
        result = XiaomiTime5.decode(value, now)
        self._validated = True
        return result

    async def write_time(self, client: BleakClient, now: datetime) -> None:
        if not self._validated:
            raise UnsupportedFormat("Read a verified five-byte clock value before writing")
        char = client.services.get_characteristic(CHARACTERISTIC)
        # The references differ; use the actual advertised write property.
        response = char is not None and "write-without-response" not in char.properties
        await client.write_gatt_char(CHARACTERISTIC, XiaomiTime5.encode(now), response=response)
        _LOGGER.debug("Xiaomi Set Time payload sent length=5")

    async def verify_time(self, client: BleakClient, now: Now) -> float:
        result = await self.read_time(client, now())
        error = result.timestamp() - now().timestamp()
        _LOGGER.debug("Xiaomi read-back final error=%+.2f s", error)
        if abs(error) > 10:
            raise VerificationError("Xiaomi read-back differs by more than 10 seconds")
        return error
