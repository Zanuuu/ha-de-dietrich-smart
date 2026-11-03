"""Codes défaut de la chaudière (notice De Dietrich NANEO EMC-S 7719021, chapitre 10).

Lettre du code : A = avertissement (la chaudière continue), H = blocage (levé seul quand la
cause disparaît), E = verrouillage (réarmement manuel). Le cloud ne transmet en général pas
le code : il se lit sur l'écran de la chaudière. Table générée par scripts/gen_faults.py.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cache
from pathlib import Path


@dataclass(frozen=True)
class FaultInfo:
    code: str
    kind: str
    label: str
    causes: tuple[str, ...]
    advice: str | None


@cache
def load() -> dict[str, FaultInfo]:
    raw = json.loads((Path(__file__).parent / "faults.json").read_text(encoding="utf-8"))
    return {code: FaultInfo(code, v["kind"], v["label"], tuple(v["causes"]), v["advice"]) for code, v in raw.items()}


def describe(code: str | None) -> FaultInfo | None:
    if not code:
        return None
    return load().get(code.strip().upper())
