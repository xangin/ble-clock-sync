"""Protocol registry, independent of hardware model and advertising format."""

from bleak import BleakClient

from .base import ClockProtocol, UnsupportedProtocol
from .cgd1 import CGD1Protocol
from .pvvx import PVVXProtocol
from .xiaomi_stock import XiaomiStockProtocol

FACTORIES: dict[str, type[PVVXProtocol] | type[XiaomiStockProtocol] | type[CGD1Protocol]] = {
    "qingping_cgd1": CGD1Protocol,
    "pvvx": PVVXProtocol,
    "xiaomi_stock": XiaomiStockProtocol,
}


def make_protocol(protocol_id: str, token: str | None = None) -> ClockProtocol:
    """Inject pairing credentials only into the protocol that defines them."""
    if protocol_id == "qingping_cgd1":
        return CGD1Protocol(token)
    return FACTORIES[protocol_id]()


async def detect_protocol(
    client: BleakClient, cached: str | None = None, token: str | None = None
) -> ClockProtocol:
    """Prefer cached selection; service/characteristic, never name, is authoritative."""
    keys = ([cached] if cached in FACTORIES else []) + [key for key in FACTORIES if key != cached]
    for key in keys:
        handler = make_protocol(key, token)
        if await handler.probe(client):
            return handler
    raise UnsupportedProtocol("No supported clock GATT service and characteristic")
