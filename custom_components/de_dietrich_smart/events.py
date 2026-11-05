"""Événements du journal, tirés de la comparaison de deux relèves."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, TypedDict

from .bdr import programs
from .bdr.models import State
from .const import LOW_PRESSURE

ZONE_MODE_FR = {"schedule": "Programme", "override": "Dérogation", "manual": "Manuel", "antifrost": "Hors-gel"}
DHW_MODE_FR = {
    "schedule": "Programme",
    "comfort": "Confort permanent",
    "eco": "Éco",
    "boost": "Boost",
    "off": "Arrêt",
}
APP_ORIGIN = "Home Assistant"
EXT_ORIGIN = "thermostat ou appli De Dietrich"

# Origine d'un changement : (clé, nouvelle valeur) → APP_ORIGIN si une commande l'attendait.
Origin = Callable[[str, Any], str]


class Event(TypedDict):
    ts: float
    kind: str
    level: str
    message: str
    data: dict[str, Any]


def event(kind: str, message: str, level: str = "info", data: dict[str, Any] | None = None) -> Event:
    return {"ts": time.time(), "kind": kind, "level": level, "message": message, "data": data or {}}


def fmt_temp(v: float | None) -> str:
    """« 17 °C », « 20,5 °C »."""
    if v is None:
        return "—"
    v = round(v, 1)
    text = f"{v:.0f}" if v == int(v) else f"{v:.1f}".replace(".", ",")
    return f"{text} °C"


def fmt_day(v: str | None) -> str:
    """« 24/12/2026 » depuis « 2026-12-24 »."""
    return "—" if not v else "/".join(reversed(v.split("-")))


def fmt_bar(v: float | None) -> str:
    return "—" if v is None else f"{v:.1f}".replace(".", ",") + " bar"


def low_pressure(pressure: float | None, pressure_ok: bool | None) -> bool:
    return pressure is not None and (pressure < LOW_PRESSURE or pressure_ok is False)


def initial(s: State) -> list[Event]:
    """Événements à la première relève."""
    a = s.appliance
    if a and a.in_error:
        return [event("defaut", f"Défaut actif : {a.error_status}", "critical", {"status": a.error_status})]
    return []


def diff(p: State, s: State, origin: Origin) -> list[Event]:
    """Événements entre la relève précédente `p` et la nouvelle `s`."""
    out: list[Event] = []
    pa, a = p.appliance, s.appliance
    if not (pa and a):
        return out

    def ev(kind: str, message: str, level: str = "info", data: dict[str, Any] | None = None) -> None:
        out.append(event(kind, message, level, data))

    if pa.in_error != a.in_error or (a.in_error and pa.error_status != a.error_status):
        if a.in_error:
            ev("defaut", f"Défaut apparu : {a.error_status}", "critical", {"status": a.error_status})
        else:
            ev("defaut", f"Défaut disparu (était {pa.error_status})", "good", {"status": pa.error_status})
    low_p, low_pp = low_pressure(a.pressure, a.pressure_ok), low_pressure(pa.pressure, pa.pressure_ok)
    if low_p and not low_pp:
        ev("pression", f"Pression basse : {fmt_bar(a.pressure)}", "warning", {"pressure": a.pressure})
    elif low_pp and not low_p and a.pressure is not None:
        ev("pression", f"Pression revenue à {fmt_bar(a.pressure)}", "good", {"pressure": a.pressure})
    if (pa.holiday_active, pa.holiday_start, pa.holiday_end) != (a.holiday_active, a.holiday_start, a.holiday_end):
        o = origin(f"{a.id}:holiday", (a.holiday_start, a.holiday_end))
        if a.holiday_start and a.holiday_end:
            msg = (
                f"Vacances en cours, retour le {fmt_day(a.holiday_end)}"
                if a.holiday_active
                else f"Vacances programmées : départ le {fmt_day(a.holiday_start)}, retour le {fmt_day(a.holiday_end)}"
            )
        else:
            msg = "Vacances terminées" if pa.holiday_active else "Vacances annulées"
        ev("reglage", msg, "info", {"origin": o})
    if pa.active_errors is not None and a.active_errors is not None:
        old = {e.get("code") for e in pa.active_errors}
        for e in a.active_errors:
            if e.get("code") not in old:
                text = f" ({e['text']})" if e.get("text") else ""
                ev("defaut", f"Erreur signalée : {e.get('code') or '?'}{text}", "critical", {"code": e.get("code")})
    if pa.online is not None and a.online is not None and pa.online != a.online:
        ev(
            "connexion",
            "Chaudière de nouveau joignable" if a.online else "Chaudière injoignable par la passerelle",
            "good" if a.online else "warning",
        )
    for pz, z in zip(pa.zones, a.zones, strict=False):
        if a.online and (pz.room_temp is None) != (z.room_temp is None):
            if z.room_temp is None:
                ev("connexion", "Température ambiante plus transmise par le cloud", "warning", {"zone": z.name})
            else:
                msg = f"Température ambiante de nouveau transmise : {fmt_temp(z.room_temp)}"
                ev("connexion", msg, "good", {"zone": z.name})
        sp_changed = pz.setpoint != z.setpoint and z.setpoint is not None
        # Un mode absent est une donnée manquante, pas un changement.
        if pz.mode != z.mode and None not in (pz.mode, z.mode):
            # Un changement de mode entraîne souvent celui de la consigne : un seul événement.
            o = origin(f"{z.id}:mode", z.mode)
            if sp_changed:
                origin(f"{z.id}:setpoint", z.setpoint)
            msg = f"Mode : {ZONE_MODE_FR.get(z_mode(pz.mode), pz.mode)} → {ZONE_MODE_FR.get(z_mode(z.mode), z.mode)}"
            if sp_changed:
                msg += f", consigne {fmt_temp(pz.setpoint)} → {fmt_temp(z.setpoint)}"
            ev("mode", msg, "info", {"origin": o, "zone": z.name})
        elif sp_changed:
            o = "programme" if z.mode == "schedule" else origin(f"{z.id}:setpoint", z.setpoint)
            msg = f"Consigne : {fmt_temp(pz.setpoint)} → {fmt_temp(z.setpoint)}"
            ev("consigne", msg, "info", {"origin": o, "zone": z.name})
        if pz.program != z.program and z.program is not None:
            o = origin(f"{z.id}:program", z.program)
            ev("programme", f"Programme actif : {pz.program} → {z.program}", "info", {"origin": o})
        old_acts = {x["n"]: x for x in pz.activities}
        for x in z.activities:
            prev = old_acts.get(x["n"])
            if prev and prev.get("setpoint") != x.get("setpoint") and x.get("setpoint") is not None:
                o = origin(f"{z.id}:activity:{x['n']}", x["setpoint"])
                name = x.get("name") or programs.ACTIVITY_NAMES.get(x["n"], f"Activité {x['n']}")
                msg = f"Activité {name} : {fmt_temp(prev.get('setpoint'))} → {fmt_temp(x['setpoint'])}"
                ev("reglage", msg, "info", {"origin": o})
        pc, c = pz.heating_curve or {}, z.heating_curve or {}
        if pc and c and (pc.get("slope"), pc.get("base")) != (c.get("slope"), c.get("base")):
            o = origin(f"{z.id}:curve", (c.get("slope"), c.get("base")))
            msg = (
                f"Courbe de chauffe : pente {pc.get('slope')} → {c.get('slope')}, "
                f"pied {fmt_temp(pc.get('base'))} → {fmt_temp(c.get('base'))}"
            ).replace(".", ",")
            ev("reglage", msg, "info", {"origin": o})
        for n, week in z.week.items():
            old_week = pz.week.get(n)
            # Programme pas encore lu (ou plus lu) : pas de changement.
            if old_week and week and old_week != week:
                o = origin(f"{z.id}:week:{n}", week)
                days = programs.days_label(programs.changed_days(old_week, week))
                ev("programme", f"Programme {n} modifié : {days}", "info", {"origin": o})
        if pz.fireplace != z.fireplace and z.fireplace is not None:
            o = origin(f"{z.id}:fireplace", z.fireplace)
            ev("mode", "Mode cheminée activé" if z.fireplace else "Mode cheminée désactivé", "info", {"origin": o})
    for pd, d in zip(pa.dhw, a.dhw, strict=False):
        if pd.mode != d.mode and None not in (pd.mode, d.mode):
            o = origin(f"{d.id}:mode", d.mode)
            msg = (
                f"Eau chaude : {DHW_MODE_FR.get(z_mode(pd.mode), pd.mode)} → {DHW_MODE_FR.get(z_mode(d.mode), d.mode)}"
            )
            ev("ecs", msg, "info", {"origin": o})
        if pd.week and d.week and pd.week != d.week:
            o = origin(f"{d.id}:week", d.week)
            days = programs.days_label(programs.changed_days(pd.week, d.week))
            ev("ecs", f"Programme eau chaude modifié : {days}", "info", {"origin": o})
        for attr, label in (("comfort_setpoint", "confort"), ("reduced_setpoint", "réduite")):
            before, after = getattr(pd, attr), getattr(d, attr)
            if before != after and None not in (before, after):
                o = origin(f"{d.id}:{attr}", after)
                ev(
                    "ecs",
                    f"Consigne eau chaude {label} : {fmt_temp(before)} → {fmt_temp(after)}",
                    "info",
                    {"origin": o},
                )
    return out


def z_mode(mode: str | None) -> str:
    return mode or ""
