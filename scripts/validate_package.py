"""Offline package contract checks; remote HACS/hassfest checks run separately in CI."""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "ble_clock_sync"


def leaf_keys(data: dict, prefix: str = "") -> set[str]:
    result: set[str] = set()
    for key, value in data.items():
        path = f"{prefix}.{key}"
        if isinstance(value, dict):
            result.update(leaf_keys(value, path))
        else:
            result.add(path)
    return result


def validate() -> None:
    """Validate install layout, metadata, translation coverage and transport rules."""
    manifest = json.loads((COMPONENT / "manifest.json").read_text())
    required = {
        "domain",
        "name",
        "version",
        "documentation",
        "issue_tracker",
        "codeowners",
        "config_flow",
        "integration_type",
        "iot_class",
        "bluetooth",
        "dependencies",
        "requirements",
    }
    assert required <= manifest.keys()
    assert manifest["domain"] == COMPONENT.name
    assert manifest["config_flow"] is True
    assert manifest["integration_type"] == "device"
    assert manifest["iot_class"] == "local_polling"
    assert "bluetooth" in manifest["dependencies"]
    assert re.fullmatch(r"\d+\.\d+\.\d+", manifest["version"])
    assert manifest["documentation"].startswith("https://github.com/")
    assert manifest["issue_tracker"] == manifest["documentation"] + "/issues"
    assert all(owner.startswith("@") for owner in manifest["codeowners"])
    assert all("==" in requirement for requirement in manifest["requirements"])
    assert all(
        "local_name" in match or "service_uuid" in match or "service_data_uuid" in match
        for match in manifest["bluetooth"]
    )
    assert all(match["connectable"] is False for match in manifest["bluetooth"])
    hacs = json.loads((ROOT / "hacs.json").read_text())
    assert hacs["render_readme"] is True and hacs["homeassistant"] == "2026.8.0"
    for name in [
        "__init__.py",
        "config_flow.py",
        "manager.py",
        "button.py",
        "sensor.py",
        "diagnostics.py",
    ]:
        assert (COMPONENT / name).is_file()
    assert (COMPONENT / "brand/icon.png").read_bytes() == (ROOT / "ble_clock_sync.png").read_bytes()
    en = json.loads((COMPONENT / "translations/en.json").read_text())
    zh = json.loads((COMPONENT / "translations/zh-Hant.json").read_text())
    assert en == json.loads((COMPONENT / "strings.json").read_text())
    assert leaf_keys(en) == leaf_keys(zh)
    assert len(en["entity"]["sensor"]) == 6
    assert len(en["entity"]["button"]) == 1
    for path in COMPONENT.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = (
                    node.func.id
                    if isinstance(node.func, ast.Name)
                    else getattr(node.func, "attr", "")
                )
                assert name not in ("BleakScanner", "BleakClient"), (
                    f"Forbidden connection/scanner constructor: {path}"
                )
    for name in [
        "README.md",
        "LICENSE",
        "docs/ARCHITECTURE.md",
        "docs/PROTOCOLS.md",
    ]:
        assert (ROOT / name).is_file()
    print("Package OK: HACS layout, manifest contract, translations, HA-only transport")


if __name__ == "__main__":
    validate()
