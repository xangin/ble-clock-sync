"""Allowlisted diagnostics; never include advertisement payloads or credentials."""

from typing import Any

from homeassistant.core import HomeAssistant

from . import BLEClockConfigEntry


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: BLEClockConfigEntry,
) -> dict[str, Any]:
    """Keep address, friendly name and proxy identity out of exported diagnostics."""
    manager = entry.runtime_data
    return {
        "address": "**REDACTED**",
        "model": manager.model,
        "firmware_family": manager.firmware_family,
        "clock_protocol": manager.protocol,
        "status": manager.status,
        "last_sync_result": manager.last_result,
        "last_sync": manager.last_sync.isoformat() if manager.last_sync else None,
        "next_sync": manager.next_sync.isoformat() if manager.next_sync else None,
        "gatt_security": "authentication_required"
        if manager.status == "authentication_required"
        else "not_determined",
        "hardware_validation": "not_verified",
    }
