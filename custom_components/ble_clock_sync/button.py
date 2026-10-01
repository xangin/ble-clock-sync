"""Button that sets a BLE clock right away."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import BLEClockConfigEntry
from .entity import BLEClockEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BLEClockConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add the sync button."""
    async_add_entities([SyncClockButton(entry.runtime_data, "sync_clock")])


class SyncClockButton(BLEClockEntity, ButtonEntity):
    """Write the Home Assistant time to the clock."""

    _attr_translation_key = "sync_clock"
    _attr_icon = "mdi:clock-sync"

    async def async_press(self) -> None:
        """Sync now and report failures in the interface."""
        await self._manager.async_sync()
