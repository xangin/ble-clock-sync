"""HA-managed connections, durable schedules/retries and clock lifecycle."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from datetime import datetime, timedelta
from typing import Any

from bleak.backends.device import BLEDevice
from bleak_retry_connector import (
    BLEAK_RETRY_EXCEPTIONS,
    BleakClientWithServiceCache,
    establish_connection,
)
from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_CORE_CONFIG_UPDATE
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.event import async_track_point_in_time, async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util
from homeassistant.util.hass_dict import HassKey

from .const import DOMAIN, MIN_CRON_INTERVAL, RETRY_DELAYS, SYNC_TIMEOUT
from .protocols import FACTORIES, detect_protocol, make_protocol
from .protocols.base import (
    ClockError,
    ClockProtocol,
    ProtocolChanged,
    UnsupportedProtocol,
    authentication_required,
    cache_invalid,
    offset_seconds,
)
from .protocols.cgd1 import CONF_AUTH_TOKEN
from .schedule import cron_expression, next_run, previous_run

_LOGGER = logging.getLogger(__name__)
SYNC_LOCK: HassKey[asyncio.Lock] = HassKey(f"{DOMAIN}_sync_lock")


async def async_remove_storage(hass: HomeAssistant, entry_id: str) -> None:
    """Remove device history when the entry is deleted."""
    await Store(hass, 1, f"{DOMAIN}.{entry_id}").async_remove()


class ClockSyncManager:
    """One device, independent of hardware model or firmware family."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, address: str) -> None:
        self.hass = hass
        self.entry = entry
        self.address = address
        self.name = entry.title
        self.model: str = entry.options.get("model", entry.data.get("model", "unknown"))
        self.last_sync: datetime | None = None
        self.next_sync: datetime | None = None
        self.protocol: str | None = None
        self.firmware_family: str | None = None
        self.status = "waiting"
        self.last_result = "never_synced"
        self.connection_source: str | None = None
        self._synced_offset: int | None = None
        self._synced_timezone: str | None = None
        self._observed_timezone = hass.config.time_zone
        self._expression = cron_expression(entry.options)
        self._credential_revision = entry.data.get("credential_revision", 0)
        self._pending = False
        self._failures = 0
        self._retry_at: datetime | None = None
        self._task: asyncio.Task[None] | None = None
        self._background: asyncio.Task[None] | None = None
        self._unsub_schedule: CALLBACK_TYPE | None = None
        self._unsub_retry: CALLBACK_TYPE | None = None
        self._unsubs: list[CALLBACK_TYPE] = []
        self._listeners: list[CALLBACK_TYPE] = []
        self._stopped = False
        self._store: Store[dict[str, Any]] = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}")

    async def async_start(self) -> None:
        """Restore pending work, register HA callbacks and catch missed schedules."""
        if stored := await self._store.async_load():
            self.last_sync = (
                dt_util.parse_datetime(stored["last_sync"]) if stored.get("last_sync") else None
            )
            self.protocol = stored.get("protocol")
            self.firmware_family = stored.get("firmware_family")
            self._synced_offset = stored.get("offset")
            self._synced_timezone = stored.get("timezone")
            self.status = stored.get("status", "waiting")
            self.last_result = stored.get("last_result", "never_synced")
            self._pending = stored.get("pending", False)
            self._failures = stored.get("failures", 0)
            self._retry_at = (
                dt_util.parse_datetime(stored["retry_at"]) if stored.get("retry_at") else None
            )
        if stored and stored.get("credential_revision", 0) != self._credential_revision:
            self.status, self.last_result = "waiting", "never_synced"
            self._pending, self._retry_at, self._failures = True, None, 0
        if stored and "drift" in stored:
            await self._save()
        self._unsubs.extend(
            [
                bluetooth.async_register_callback(
                    self.hass,
                    self._async_advertisement,
                    bluetooth.BluetoothCallbackMatcher(address=self.address, connectable=False),
                    bluetooth.BluetoothScanningMode.PASSIVE,
                ),
                async_track_time_interval(
                    self.hass, self._async_check_offset, timedelta(minutes=1)
                ),
                self.hass.bus.async_listen(EVENT_CORE_CONFIG_UPDATE, self._async_config_changed),
            ]
        )
        self._async_schedule_next()
        self._arm_retry()
        blocked = self.last_result in ("authentication_required", "unsupported_protocol")
        missed = (
            self._expression is not None
            and self.last_sync is not None
            and (
                previous_run(self._expression, dt_util.now()).timestamp()
                > self.last_sync.timestamp()
            )
        )
        changed = self._timezone_changed()
        if not blocked and (self._pending or self.last_sync is None or missed or changed):
            self.async_request_sync()

    async def async_stop(self) -> None:
        """Cancel callbacks and await connection cleanup before unloading."""
        self._stopped = True
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        self._cancel_timers()
        for task in (self._background, self._task):
            if task and not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
        await self._save()

    @callback
    def async_add_listener(self, update_callback: CALLBACK_TYPE) -> CALLBACK_TYPE:
        self._listeners.append(update_callback)
        return lambda: self._listeners.remove(update_callback)

    @callback
    def _notify(self) -> None:
        for listener in list(self._listeners):
            listener()

    def _state(self) -> dict[str, Any]:
        return {
            "last_sync": self.last_sync.isoformat() if self.last_sync else None,
            "credential_revision": self._credential_revision,
            "protocol": self.protocol,
            "firmware_family": self.firmware_family,
            "offset": self._synced_offset,
            "timezone": self._synced_timezone,
            "status": self.status,
            "last_result": self.last_result,
            "pending": self._pending,
            "failures": self._failures,
            "retry_at": self._retry_at.isoformat() if self._retry_at else None,
        }

    async def _save(self) -> None:
        await self._store.async_save(self._state())

    @callback
    def async_request_sync(self) -> None:
        """Automatic requests respect backoff and blocked authentication failures."""
        if self._stopped or self.last_result in ("authentication_required", "unsupported_protocol"):
            return
        if (
            not self._pending
            and self.last_sync is not None
            and not self._timezone_changed()
            and dt_util.utcnow() < self.last_sync + MIN_CRON_INTERVAL
        ):
            self._retry_at = self.last_sync + MIN_CRON_INTERVAL
            self._arm_retry()
        self._pending = True
        self._store.async_delay_save(self._state, 1)
        if self._retry_at and dt_util.utcnow() < self._retry_at:
            return
        self._start_background()

    @callback
    def _start_background(self) -> None:
        if self._stopped or (self._background and not self._background.done()):
            return
        self._background = self.entry.async_create_background_task(
            self.hass, self._background_sync(), f"{DOMAIN} scheduled sync"
        )

    async def _background_sync(self) -> None:
        try:
            await self.async_sync()
        except HomeAssistantError:
            # The classified, secret-free error is already exposed by diagnostics.
            _LOGGER.debug(
                "Clock sync deferred/result=%s retry=%s", self.last_result, self._retry_at
            )

    async def async_sync(self) -> None:
        """Coalesce concurrent manual and automatic requests into one operation."""
        if self._stopped:
            return
        if self._task is None or self._task.done():
            self._task = self.hass.async_create_task(self._perform_sync(), f"{DOMAIN} connection")
        await asyncio.shield(self._task)

    def _device(self) -> BLEDevice | None:
        return bluetooth.async_ble_device_from_address(self.hass, self.address, connectable=True)

    async def _perform_sync(self) -> None:
        self._pending = True
        async with self.hass.data.setdefault(SYNC_LOCK, asyncio.Lock()):
            device = self._device()
            if device is None:
                visible = bluetooth.async_address_present(
                    self.hass, self.address, connectable=False
                )
                await self._failure(
                    "no_active_connection" if visible else "not_in_range", retry=False
                )
            assert device is not None
            info = bluetooth.async_last_service_info(self.hass, self.address, connectable=True)
            self.connection_source = info.source if info else "unknown"
            _LOGGER.debug(
                "Device found model=%s connection source=%s", self.model, self.connection_source
            )
            try:
                async with asyncio.timeout(SYNC_TIMEOUT):
                    await self._connected_sync(device)
            except (*BLEAK_RETRY_EXCEPTIONS, TimeoutError, ClockError) as error:
                if authentication_required(error):
                    await self._failure("authentication_required", retry=False, blocked=True)
                elif isinstance(error, UnsupportedProtocol) or cache_invalid(error):
                    await self._failure("unsupported_protocol", retry=False, blocked=True)
                else:
                    await self._failure("sync_failed", retry=True)
            self.last_sync = dt_util.utcnow()
            self._synced_offset = offset_seconds(dt_util.now())
            self._synced_timezone = self.hass.config.time_zone
            self._pending = False
            self._failures = 0
            self._retry_at = None
            self.status = "synced"
            self.last_result = "success"
            self._arm_retry()
            self._async_schedule_next()
            self._update_device()
            await self._save()
            self._notify()
            _LOGGER.info(
                "%s clock synchronized successfully; firmware=%s protocol=%s",
                self.model,
                self.firmware_family,
                self.protocol,
            )

    async def _failure(self, result: str, *, retry: bool, blocked: bool = False) -> None:
        self.last_result = result
        self.status = (
            "authentication_required"
            if result == "authentication_required"
            else ("waiting" if result in ("not_in_range", "no_active_connection") else "failed")
        )
        self._pending = not blocked
        self._retry_at = None
        if retry:
            self._failures += 1
            self._retry_at = (
                dt_util.utcnow() + RETRY_DELAYS[min(self._failures, len(RETRY_DELAYS)) - 1]
            )
        self._arm_retry()
        self._update_device()
        await self._save()
        self._notify()
        _LOGGER.debug("Clock result=%s retry=%s", result, self._retry_at)
        raise HomeAssistantError(translation_domain=DOMAIN, translation_key=result)

    async def _connected_sync(self, device: BLEDevice) -> None:
        for attempt in range(2):
            # Prefer a newly selected proxy, preserving HA's BLEDevice details.
            def latest_device() -> BLEDevice:
                return self._device() or device

            client = await establish_connection(
                BleakClientWithServiceCache,
                latest_device(),
                self.name,
                max_attempts=3,
                ble_device_callback=latest_device,
            )
            try:
                _LOGGER.debug(
                    "Services discovered: %s", [service.uuid for service in client.services]
                )
                if self.protocol in FACTORIES:
                    cached: ClockProtocol = make_protocol(
                        self.protocol, self.entry.data.get(CONF_AUTH_TOKEN)
                    )
                    if not await cached.probe(client):
                        raise ProtocolChanged("Cached service or characteristic not found")
                    handler = cached
                else:
                    handler = await detect_protocol(
                        client, token=self.entry.data.get(CONF_AUTH_TOKEN)
                    )
                self.protocol, self.firmware_family = handler.id, handler.firmware_family
                self._update_device()
                self._notify()
                _LOGGER.info(
                    "%s detected; firmware family=%s clock protocol=%s",
                    self.model,
                    self.firmware_family,
                    self.protocol,
                )
                if not handler.supports_model(self.model):
                    raise UnsupportedProtocol("Hardware/firmware combination is outside v1 scope")
                await handler.read_time(client, dt_util.now())
                await handler.write_time(client, dt_util.now())
                await handler.verify_time(client, dt_util.now)
                return
            except Exception as error:
                if cache_invalid(error):
                    self.protocol = self.firmware_family = None
                    await self._save()
                    await client.clear_cache()
                    _LOGGER.debug(
                        "Protocol/service cache invalidated; reconnect attempt=%d", attempt + 1
                    )
                    if attempt == 0:
                        continue
                raise
            finally:
                await client.disconnect()

    @callback
    def _update_device(self) -> None:
        registry = dr.async_get(self.hass)
        if device := registry.async_get_device_by_identifier(
            (DOMAIN, self.address), self.entry.entry_id
        ):
            registry.async_update_device(
                device.id, model=self.model, sw_version=self.firmware_family
            )

    @callback
    def _async_advertisement(
        self, info: bluetooth.BluetoothServiceInfoBleak, _change: bluetooth.BluetoothChange
    ) -> None:
        if (
            self._pending
            and self._device() is not None
            and (self._retry_at is None or dt_util.utcnow() >= self._retry_at)
        ):
            _LOGGER.debug("Device advertisement received source=%s; pending sync", info.source)
            self._start_background()

    def _timezone_changed(self) -> bool:
        return self._synced_offset is not None and (
            offset_seconds(dt_util.now()) != self._synced_offset
            or self.hass.config.time_zone != self._synced_timezone
        )

    @callback
    def _async_config_changed(self, _event: Event) -> None:
        self._async_check_offset(dt_util.now())

    @callback
    def _async_check_offset(self, _now: datetime) -> None:
        if self._observed_timezone != self.hass.config.time_zone:
            self._observed_timezone = self.hass.config.time_zone
            self._async_schedule_next()
            self._notify()
        if self._timezone_changed() and not self._pending:
            self.async_request_sync()

    @callback
    def _async_schedule_next(self) -> None:
        if self._unsub_schedule:
            self._unsub_schedule()
            self._unsub_schedule = None
        self.next_sync = None
        if self._expression is None or self._stopped:
            return
        after = dt_util.now()
        # Guard actual elapsed time even across DST folds and unusual cron calendars.
        if self.last_sync:
            earliest = self.last_sync + MIN_CRON_INTERVAL - timedelta(microseconds=1)
            if earliest.timestamp() > after.timestamp():
                after = dt_util.as_local(earliest)
        self.next_sync = next_run(self._expression, after)
        self._unsub_schedule = async_track_point_in_time(self.hass, self._scheduled, self.next_sync)

    @callback
    def _scheduled(self, _now: datetime) -> None:
        self._unsub_schedule = None
        self._async_schedule_next()
        self.async_request_sync()
        self._notify()

    @callback
    def _arm_retry(self) -> None:
        if self._unsub_retry:
            self._unsub_retry()
            self._unsub_retry = None
        if self._retry_at and self._retry_at > dt_util.utcnow() and not self._stopped:
            self._unsub_retry = async_track_point_in_time(
                self.hass, self._retry_due, self._retry_at
            )

    @callback
    def _retry_due(self, _now: datetime) -> None:
        self._unsub_retry = None
        if self._pending:
            self._start_background()

    @callback
    def _cancel_timers(self) -> None:
        for unsub in (self._unsub_schedule, self._unsub_retry):
            if unsub:
                unsub()
        self._unsub_schedule = self._unsub_retry = None
