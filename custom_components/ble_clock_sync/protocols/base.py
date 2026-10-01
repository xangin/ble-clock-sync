"""Transport-independent clock contract and safe protocol errors."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol

from bleak import BleakClient
from bleak.exc import BleakCharacteristicNotFoundError

Now = Callable[[], datetime]


class ClockError(Exception):
    """An expected clock operation failure (messages must contain no secrets)."""


class AuthenticationRequired(ClockError):
    """A protocol handshake could not prove authentication."""


class UnsupportedProtocol(ClockError):
    """No supported clock service, command or format."""


class UnsupportedFormat(UnsupportedProtocol):
    """An unverified stock payload must never be written."""


class ProtocolChanged(ClockError):
    """Invalidate GATT/protocol cache before one fresh connection."""


class VerificationError(ClockError):
    """The device did not confirm the expected time."""


def authentication_required(error: Exception) -> bool:
    """Classify explicit ATT/security errors; never infer from advertisement data."""
    if isinstance(error, AuthenticationRequired):
        return True
    value = str(error).lower().replace("_", " ")
    return any(
        token in value
        for token in (
            "insufficient authentication",
            "insufficient encryption",
            "insufficient authorization",
            "authentication required",
            "authorization required",
            "not authorized",
            "notauthorized",
            "authenticationfailed",
            "authentication failed",
            "authentication failure",
            "pin or key missing",
            "insufficient security",
            "gatt insuf authentication",
            "gatt insuf encryption",
            "gatt insuf authorization",
            "att error: 0x05",
            "att error: 0x08",
            "att error: 0x0f",
        )
    )


def cache_invalid(error: Exception) -> bool:
    """Recognize only service/characteristic/command changes, not general failures."""
    return isinstance(error, (ProtocolChanged, BleakCharacteristicNotFoundError)) or any(
        token in str(error).lower()
        for token in ("service changed", "characteristic not found", "unsupported command")
    )


def offset_seconds(moment: datetime) -> int:
    """Require aware HA time, independent of the process timezone."""
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("An aware datetime is required")
    return int((moment.utcoffset() or timedelta()).total_seconds())


def local_epoch(moment: datetime) -> float:
    """Counter whose UTC calendar fields equal the desired local clock display."""
    return moment.timestamp() + offset_seconds(moment)


def displayed_time(epoch: float, now: datetime) -> datetime:
    """Convert a displayed counter to an instant using HA's current offset."""
    return datetime.fromtimestamp(epoch - offset_seconds(now), UTC)


class ClockProtocol(Protocol):
    """A handler uses an existing HA connection; it never creates one."""

    id: str
    firmware_family: str

    def supports_model(self, model: str) -> bool: ...
    async def probe(self, client: BleakClient) -> bool: ...
    async def read_time(self, client: BleakClient, now: datetime) -> datetime | None: ...
    async def write_time(self, client: BleakClient, now: datetime) -> None: ...
    async def verify_time(self, client: BleakClient, now: Now) -> float | None: ...
