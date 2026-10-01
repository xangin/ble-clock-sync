"""Sensors describing the clock sync of a BLE."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType

from . import BLEClockConfigEntry
from .const import RESULTS, STATUSES
from .entity import BLEClockEntity
from .manager import ClockSyncManager


@dataclass(frozen=True, kw_only=True)
class BLEClockSensorDescription(SensorEntityDescription):
    """Sensor reading one value of the clock manager."""

    value_fn: Callable[[ClockSyncManager], StateType | datetime]


SENSORS: tuple[BLEClockSensorDescription, ...] = (
    BLEClockSensorDescription(
        key="clock_protocol",
        translation_key="clock_protocol",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_class=SensorDeviceClass.ENUM,
        options=["pvvx", "xiaomi_stock", "qingping_cgd1"],
        value_fn=lambda manager: manager.protocol,
    ),
    BLEClockSensorDescription(
        key="firmware_family",
        translation_key="firmware_family",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_class=SensorDeviceClass.ENUM,
        options=["pvvx", "xiaomi_stock", "qingping_stock"],
        value_fn=lambda manager: manager.firmware_family,
    ),
    BLEClockSensorDescription(
        key="last_sync_result",
        translation_key="last_sync_result",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_class=SensorDeviceClass.ENUM,
        options=RESULTS,
        value_fn=lambda manager: manager.last_result,
    ),
    BLEClockSensorDescription(
        key="last_sync",
        translation_key="last_sync",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda manager: manager.last_sync,
    ),
    BLEClockSensorDescription(
        key="next_sync",
        translation_key="next_sync",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda manager: manager.next_sync,
    ),
    BLEClockSensorDescription(
        key="sync_status",
        translation_key="sync_status",
        device_class=SensorDeviceClass.ENUM,
        options=STATUSES,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda manager: manager.status,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BLEClockConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add the sync sensors."""
    async_add_entities(BLEClockSensor(entry.runtime_data, description) for description in SENSORS)


class BLEClockSensor(BLEClockEntity, SensorEntity):
    """Sync information of one clock."""

    entity_description: BLEClockSensorDescription

    def __init__(self, manager: ClockSyncManager, description: BLEClockSensorDescription) -> None:
        """Describe the sensor."""
        super().__init__(manager, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> StateType | datetime:
        """Return the current value."""
        return self.entity_description.value_fn(self._manager)
