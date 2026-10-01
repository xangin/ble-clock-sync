"""Keep BLE clocks on time from Home Assistant."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN
from .manager import ClockSyncManager, async_remove_storage

PLATFORMS: list[Platform] = [Platform.BUTTON, Platform.SENSOR]

type BLEClockConfigEntry = ConfigEntry[ClockSyncManager]


async def async_setup_entry(hass: HomeAssistant, entry: BLEClockConfigEntry) -> bool:
    """Set up one BLE clock."""
    # Retire only this entry's old drift entity, including user-renamed entities.
    registry = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if (
            entity.platform == DOMAIN
            and entity.unique_id == f"{entry.data[CONF_ADDRESS]}_clock_drift"
        ):
            registry.async_remove(entity.entity_id)
    manager = ClockSyncManager(hass, entry, entry.data[CONF_ADDRESS])
    entry.runtime_data = manager
    await manager.async_start()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def _async_options_updated(hass: HomeAssistant, entry: BLEClockConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: BLEClockConfigEntry) -> bool:
    """Unload a config entry."""
    if await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.async_stop()
        return True
    return False


async def async_remove_entry(hass: HomeAssistant, entry: BLEClockConfigEntry) -> None:
    """Forget the sync history of a removed clock."""
    await async_remove_storage(hass, entry.entry_id)
