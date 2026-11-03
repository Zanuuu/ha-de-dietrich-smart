"""Traductions : en = strings.json, fr complet (mêmes clés)."""

import json
from pathlib import Path

D = Path(__file__).resolve().parents[1] / "custom_components/de_dietrich_smart"


def keys(tree: dict, prefix: str = "") -> set[str]:
    out: set[str] = set()
    for k, v in tree.items():
        path = f"{prefix}.{k}" if prefix else k
        out |= keys(v, path) if isinstance(v, dict) else {path}
    return out


def test_en_is_strings() -> None:
    assert json.loads((D / "translations/en.json").read_text()) == json.loads((D / "strings.json").read_text())


def test_fr_complete() -> None:
    strings = keys(json.loads((D / "strings.json").read_text()))
    assert keys(json.loads((D / "translations/fr.json").read_text())) == strings


def test_config_flow_strings() -> None:
    k = keys(json.loads((D / "strings.json").read_text()))
    for step in ("user", "reauth_confirm", "reconfigure"):
        assert f"config.step.{step}.data.password" in k
    for err in ("invalid_auth", "cannot_connect", "unknown", "no_appliance"):
        assert f"config.error.{err}" in k
    for reason in ("already_configured", "reauth_successful", "reconfigure_successful", "unique_id_mismatch"):
        assert f"config.abort.{reason}" in k
    for opt in ("default_duration", "emitter", "connection", "max_floor_flow", "base_temp", "weather_entity"):
        assert f"options.step.init.data.{opt}" in k
