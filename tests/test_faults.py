"""Table des codes défaut (notice EMC-S 7719021)."""

from custom_components.de_dietrich_smart import faults


def test_faults_json_complete() -> None:
    table = faults.load()
    assert len(table) == 66
    for code, f in table.items():
        assert f.kind in {"A", "H", "E"} and code.startswith(f.kind)
        assert f.label and f.causes


def test_describe() -> None:
    f = faults.describe("A02.06")
    assert f is not None and f.label == "Avertissement de pression d'eau actif"
    assert f.advice is not None and "1,5 et 2 bar" in f.advice
    assert faults.describe("h01.00") is not None
    assert faults.describe("Z99.99") is None and faults.describe(None) is None
