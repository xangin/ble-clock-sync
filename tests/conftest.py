"""Real HA fixtures with all clock Bluetooth I/O mocked."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Generator
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from homeassistant.components.bluetooth import BluetoothChange, BluetoothServiceInfoBleak
from homeassistant.core import HomeAssistant

from custom_components.ble_clock_sync.protocols import pvvx, xiaomi_stock

ADDRESS = "E7:2E:01:AB:CD:EF"
TITLE = "LYWSD02 (CDEF)"


def service_info(
    address: str = ADDRESS, name: str = "LYWSD02", source: str = "local", connectable: bool = True
) -> BluetoothServiceInfoBleak:
    return BluetoothServiceInfoBleak(
        name=name,
        address=address,
        rssi=-60,
        manufacturer_data={},
        service_data={},
        service_uuids=[],
        source=source,
        device=BLEDevice(address, name, {"source": source}),
        advertisement=AdvertisementData(name, {}, {}, [], None, -60, ()),
        connectable=connectable,
        time=0,
        tx_power=None,
    )


class Services:
    def __init__(self, protocol: str) -> None:
        module = pvvx if protocol == "pvvx" else xiaomi_stock
        self.service_uuid = module.SERVICE
        self.char = SimpleNamespace(
            uuid=module.CHARACTERISTIC, properties=["read", "notify", "write-without-response"]
        )

    def get_service(self, uuid: str) -> Services | None:
        return self if uuid == self.service_uuid else None

    def get_characteristic(self, uuid: str) -> Any:
        return self.char if uuid == self.char.uuid else None

    def __iter__(self):
        return iter([SimpleNamespace(uuid=self.service_uuid)])


class FakeClock:
    def __init__(self, protocol: str = "xiaomi_stock") -> None:
        self.services = Services(protocol)
        self.value = bytes(5)
        self.protocol = protocol
        self.writes: list[bytes] = []
        self.disconnects = 0
        self.clear_count = 0
        self.is_connected = True
        self.notify: Callable[..., None] | None = None
        self.error: Exception | None = None
        self.response_length = 9
        self.unsupported = False
        self.silent = False
        self.corrupt = False
        self.unsolicited = False
        self.stop_count = 0

    async def read_gatt_char(self, uuid: str) -> bytearray:
        if self.error:
            raise self.error
        return bytearray(self.value)

    async def start_notify(self, uuid: str, callback: Callable[..., None]) -> None:
        if self.error:
            raise self.error
        self.notify = callback

    async def stop_notify(self, uuid: str) -> None:
        self.stop_count += 1
        self.notify = None

    async def write_gatt_char(self, uuid: str, data: bytes, response: bool) -> None:
        if self.error:
            raise self.error
        self.writes.append(bytes(data))
        if self.protocol == "xiaomi_stock":
            assert len(data) == 5
            if not self.corrupt:
                self.value = bytes(data)
        else:
            assert response is False
            assert data[0] == 0x23
            if len(data) == 5 and not self.corrupt:
                self.value = bytes(data[1:])
            value = b"\x23" + self.value[:4]
            if self.response_length == 9:
                value += self.value[:4]
            if self.unsupported:
                value = b"\x23\xff"
            if self.notify and not self.silent:
                if self.unsolicited:
                    self.notify(None, bytearray(b"\x10secret_bindkey"))
                self.notify(None, bytearray(value))

    async def disconnect(self) -> None:
        self.disconnects += 1
        self.is_connected = False

    async def clear_cache(self) -> None:
        self.clear_count += 1


class BluetoothHarness:
    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock
        self.in_range = True
        self.passive_only = False
        self.source = "local"
        self.connect_error: Exception | None = None
        self.connections = 0
        self.devices: list[BLEDevice] = []
        self._callbacks: list[Any] = []
        self.replacements: list[FakeClock] = []

    def device(
        self, hass: HomeAssistant, address: str, connectable: bool = True
    ) -> BLEDevice | None:
        if not self.in_range or (connectable and self.passive_only):
            return None
        return service_info(address=address, source=self.source).device

    def present(self, hass: HomeAssistant, address: str, connectable: bool = True) -> bool:
        return self.in_range and not (connectable and self.passive_only)

    def info(
        self, hass: HomeAssistant, address: str, connectable: bool = True
    ) -> BluetoothServiceInfoBleak | None:
        return (
            service_info(address=address, source=self.source)
            if self.present(hass, address, connectable)
            else None
        )

    async def connect(
        self, client_class: Any, device: BLEDevice, name: str, **kwargs: Any
    ) -> FakeClock:
        await asyncio.sleep(0)  # Real BLE I/O yields, even with HA eager tasks.
        self.connections += 1
        self.devices.append(device)
        assert kwargs["ble_device_callback"]().address == device.address
        if self.connect_error:
            raise self.connect_error
        if self.replacements:
            self.clock = self.replacements.pop(0)
        self.clock.is_connected = True
        return self.clock

    def register(
        self, hass: HomeAssistant, callback: Any, matcher: Any, mode: Any, **kwargs: Any
    ) -> Callable[[], None]:
        self._callbacks.append(callback)
        return lambda: self._callbacks.remove(callback)

    def advertise(self) -> None:
        for callback in list(self._callbacks):
            callback(service_info(source=self.source), BluetoothChange.ADVERTISEMENT)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    pass


@pytest.fixture(autouse=True)
def bluetooth_mocked(mock_bluetooth: None) -> None:
    pass


@pytest.fixture
async def paris(hass: HomeAssistant) -> None:
    await hass.config.async_set_time_zone("Europe/Paris")


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def ble(clock: FakeClock) -> Generator[BluetoothHarness]:
    harness = BluetoothHarness(clock)
    with (
        patch(
            "custom_components.ble_clock_sync.manager.establish_connection",
            side_effect=harness.connect,
        ),
        patch(
            "homeassistant.components.bluetooth.async_ble_device_from_address",
            side_effect=harness.device,
        ),
        patch(
            "homeassistant.components.bluetooth.async_address_present", side_effect=harness.present
        ),
        patch(
            "homeassistant.components.bluetooth.async_last_service_info", side_effect=harness.info
        ),
        patch(
            "homeassistant.components.bluetooth.async_register_callback",
            side_effect=harness.register,
        ),
    ):
        yield harness
