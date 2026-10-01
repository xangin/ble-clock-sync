"""Conservative advertisement candidates; GATT determines firmware protocol."""

from homeassistant.components.bluetooth import BluetoothServiceInfoBleak

from .protocols.cgd1 import ADVERTISEMENT_SERVICE
from .protocols.cgd1 import SERVICE as CGD1_SERVICE
from .protocols.pvvx import SERVICE as PVVX_SERVICE
from .protocols.xiaomi_stock import SERVICE as XIAOMI_SERVICE

MODELS = ["unknown", "LYWSD02", "LYWSD02MMC", "MHO-C303", "MJWSD05MMC", "CGD1"]
PREFIXES = ("LYWSD02", "MHO-C303", "MJWSD05MMC", "ATC_", "BTH_", "PVVX_", "CGD1", "Qingping CGD1")


def is_cgd1(info: BluetoothServiceInfoBleak) -> bool:
    name = info.advertisement.local_name or info.name or ""
    data = info.service_data.get(ADVERTISEMENT_SERVICE, b"")
    return (
        name.startswith(("CGD1", "Qingping CGD1"))
        or CGD1_SERVICE in {uuid.lower() for uuid in info.service_uuids}
        or (len(data) >= 17 and data[1] == 0x0C)
    )


def candidate_reason(info: BluetoothServiceInfoBleak) -> str | None:
    """BTHome/fdcd UUID alone does not establish clock capability."""
    if is_cgd1(info):
        return "cgd1_name_service_or_model"
    name = info.advertisement.local_name or info.name or ""
    if any(name.startswith(prefix) for prefix in PREFIXES):
        return "known_local_name"
    services = {uuid.lower() for uuid in info.service_uuids}
    if services.intersection({PVVX_SERVICE, XIAOMI_SERVICE}):
        return "clock_service_uuid"
    return None


def model_from_name(name: str) -> str:
    if name.startswith("Qingping CGD1"):
        return "CGD1"
    return next(
        (model for model in sorted(MODELS[1:], key=len, reverse=True) if name.startswith(model)),
        "unknown",
    )


def model_from_info(info: BluetoothServiceInfoBleak) -> str:
    return "CGD1" if is_cgd1(info) else model_from_name(info.name)
