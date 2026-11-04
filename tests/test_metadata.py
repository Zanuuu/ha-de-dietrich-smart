"""Contrôles de hassfest et de HACS rejoués par la CI GitLab (avant le miroir GitHub)."""

import json
import re
import struct
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
INTEGRATION = ROOT / "custom_components" / "de_dietrich_smart"
PLACEHOLDER = re.compile(r"\{([^{}]*)\}")
IDENTIFIER = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]*")


def test_manifest_keys_sorted() -> None:
    keys = list(json.loads((INTEGRATION / "manifest.json").read_text()))
    assert keys[:2] == ["domain", "name"] and keys[2:] == sorted(keys[2:])


def _strings(node, path=""):
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _strings(v, f"{path}.{k}" if path else k)
    elif isinstance(node, str):
        yield path, node


@pytest.mark.parametrize("name", ["strings.json", "translations/en.json", "translations/fr.json"])
def test_translation_placeholders_are_identifiers(name: str) -> None:
    for path, text in _strings(json.loads((INTEGRATION / name).read_text())):
        for ph in PLACEHOLDER.findall(text):
            assert IDENTIFIER.fullmatch(ph), f"{name} {path}: {{{ph}}}"


@pytest.mark.parametrize(("name", "size"), [("icon.png", 256), ("icon@2x.png", 512)])
def test_brand_icon(name: str, size: int) -> None:
    data = (INTEGRATION / "brand" / name).read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert struct.unpack(">II", data[16:24]) == (size, size)


def test_license_is_plain_mit() -> None:
    # Un paragraphe ajouté empêche GitHub (et HACS) de reconnaître la licence.
    assert (ROOT / "LICENSE").read_text().rstrip().endswith("OTHER DEALINGS IN THE\nSOFTWARE.")
