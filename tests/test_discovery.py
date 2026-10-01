"""Advertisement candidates do not determine firmware or request bindkeys."""

from custom_components.ble_clock_sync.discovery import candidate_reason, model_from_name
from custom_components.ble_clock_sync.protocols.pvvx import SERVICE

from .conftest import service_info


def test_names_and_cgd1():
    for name in ["LYWSD02", "LYWSD02MMC", "MHO-C303", "MJWSD05MMC", "ATC_ABC", "BTH_ABC"]:
        assert candidate_reason(service_info(name=name)) == "known_local_name"
    assert candidate_reason(service_info(name="CGD1")) == "cgd1_name_service_or_model"
    assert model_from_name("LYWSD02MMC") == "LYWSD02MMC"
    assert model_from_name("BTH_123456") == "unknown"


def test_clock_service_and_encrypted_bthome():
    info = service_info(name="Renamed")
    info.service_uuids.append(SERVICE)
    assert candidate_reason(info) == "clock_service_uuid"
    unrelated = service_info(name="Other sensor")
    unrelated.service_data["0000fcd2-0000-1000-8000-00805f9b34fb"] = b"\x41encrypted"
    assert candidate_reason(unrelated) is None
    clock = service_info(name="BTH_123456")
    clock.service_data.update(unrelated.service_data)
    assert candidate_reason(clock) == "known_local_name"
