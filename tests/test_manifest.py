"""Manifeste de l'intégration et fichier HACS."""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_manifest_fields() -> None:
    m = json.loads((ROOT / "custom_components/de_dietrich_smart/manifest.json").read_text())
    assert m["domain"] == "de_dietrich_smart"
    assert m["iot_class"] == "cloud_polling"
    assert m["config_flow"] is True
    assert m["requirements"] == []
    assert re.fullmatch(r"\d+\.\d+\.\d+", m["version"])


def test_hacs_json() -> None:
    h = json.loads((ROOT / "hacs.json").read_text())
    assert h["homeassistant"] == "2026.10.0"
    assert h["zip_release"] is True and h["filename"] == "de_dietrich_smart.zip"
    assert h["render_readme"] is True


def test_release_workflow_zips_the_integration() -> None:
    wf = (ROOT / ".github/workflows/release.yml").read_text()
    assert "de_dietrich_smart.zip" in wf and "custom_components/de_dietrich_smart" in wf
