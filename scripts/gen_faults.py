"""Génère custom_components/de_dietrich_smart/faults.json depuis web/codes.js de l'application.

Usage : python3 scripts/gen_faults.py <chemin de codes.js>
La table vient de la notice De Dietrich NANEO EMC-S 7719021 (chapitre 10).
"""

import ast
import json
import re
import sys
from pathlib import Path

src = Path(sys.argv[1]).read_text()
consts = {m[1]: ast.literal_eval(m[2]) for m in re.finditer(r'^const (\w+) = ("(?:[^"\\]|\\.)*");', src, re.M)}
block = src[src.index("const CODES = {") + len("const CODES = ") : src.index("};", src.index("const CODES = {")) + 1]
block = "\n".join(line for line in block.splitlines() if not line.strip().startswith("//"))
for name, value in consts.items():
    block = re.sub(rf"\b{name}\b", repr(value), block)
codes = ast.literal_eval(block)
out = {
    code: {"kind": code[0], "label": v[0], "causes": v[1], "advice": v[2] if len(v) > 2 else None}
    for code, v in sorted(codes.items())
}
dest = Path(__file__).resolve().parents[1] / "custom_components/de_dietrich_smart/faults.json"
dest.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n")
print(f"{len(out)} codes → {dest}")
