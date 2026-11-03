"""Programmes horaires du thermostat : changements de consigne par jour et activités.

Porté de l'application ancienne application.

Format commun (celui du cloud BDR Thermea, normalisé) : une liste de changements
{"day": 0-6, "time": "HH:MM", "activity": n, "setpoint": float | None}. Jour 0 =
dimanche, 1 = lundi … 6 = samedi. `setpoint` n'est renseigné que si le changement porte
sa propre consigne ; sinon la consigne est celle de l'activité.

Le cloud donne les activités (nom, consigne) par une adresse à part ; si elle manque,
l'application relève la consigne quand le programme suit une activité (consigne du
programme au moment de la relève), ou l'utilisateur la saisit dans Réglages.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

# Noms des activités du SMART TC° (appli De Dietrich : Sleep, Home, Away, Morning, Evening).
ACTIVITY_NAMES = {1: "Nuit", 2: "Maison", 3: "Absent", 4: "Matin", 5: "Soir"}
# Pas de relevé juste après un changement : le cloud peut encore donner l'ancienne consigne.
SETTLE = timedelta(minutes=5)


def cloud_day(dt: datetime) -> int:
    """Jour au format du programme : 0 = dimanche."""
    return dt.isoweekday() % 7


def _minutes(sp: dict[str, Any]) -> int:
    h, m = str(sp["time"]).split(":")[:2]
    return sp["day"] * 1440 + int(h) * 60 + int(m)


def current_switch(week: list[dict[str, Any]], local: datetime) -> tuple[dict[str, Any] | None, int]:
    """Changement en vigueur à cet instant (heure locale) et minutes écoulées depuis."""
    if not week:
        return None, 0
    now = cloud_day(local) * 1440 + local.hour * 60 + local.minute
    ordered = sorted(week, key=_minutes)
    past = [sp for sp in ordered if _minutes(sp) <= now]
    if past:
        sp = past[-1]
        return sp, now - _minutes(sp)
    # Avant le premier changement de la semaine : le dernier de la semaine précédente.
    sp = ordered[-1]
    return sp, now + 7 * 1440 - _minutes(sp)


def next_switch(week: list[dict[str, Any]], local: datetime) -> dict[str, Any] | None:
    """Prochain changement après cet instant (heure locale)."""
    if not week:
        return None
    now = cloud_day(local) * 1440 + local.hour * 60 + local.minute
    ordered = sorted(week, key=_minutes)
    later = [sp for sp in ordered if _minutes(sp) > now]
    return later[0] if later else ordered[0]


def load_activities(raw: str | None) -> dict[int, dict[str, Any]]:
    try:
        data = json.loads(raw or "{}")
    except ValueError:
        return {}
    return {int(k): v for k, v in data.items() if str(k).isdigit() and isinstance(v, dict)}


def dump_activities(acts: dict[int, dict[str, Any]]) -> str:
    return json.dumps({str(k): v for k, v in sorted(acts.items())})


def _record(acts: dict[int, dict[str, Any]], n: int, value: float, local: datetime) -> tuple[int, float | None] | None:
    old = acts.get(n, {})
    if old.get("setpoint") == value and old.get("origin") == "relevée":
        return None
    acts[n] = {"setpoint": value, "origin": "relevée", "at": int(local.timestamp())}
    return n, old.get("setpoint")


def learn(
    acts: dict[int, dict[str, Any]],
    week: list[dict[str, Any]],
    mode: str | None,
    schedule_setpoint: float | None,
    next_setpoint: float | None,
    local: datetime,
) -> list[tuple[int, float | None]]:
    """Relève la consigne de l'activité en cours et celle du prochain changement, quand la
    zone suit son programme. Retourne les changements de la table : (activité, ancienne
    consigne)."""
    if mode != "schedule":
        return []
    changes = []
    sp, since = current_switch(week, local)
    if sp and sp.get("setpoint") is None and schedule_setpoint is not None and since >= SETTLE.total_seconds() / 60:
        changes.append(_record(acts, int(sp["activity"]), schedule_setpoint, local))
    nxt = next_switch(week, local)
    if nxt and nxt.get("setpoint") is None and next_setpoint is not None and since >= SETTLE.total_seconds() / 60:
        if not (sp and int(nxt["activity"]) == int(sp["activity"]) and sp.get("setpoint") is None):
            changes.append(_record(acts, int(nxt["activity"]), next_setpoint, local))
    return [c for c in changes if c]


def public(
    acts: dict[int, dict[str, Any]],
    weeks: list[list[dict[str, Any]]],
    cloud: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Activités connues ou utilisées, pour l'interface. `cloud` : activités lues sur le
    thermostat (nom et consigne réels), prioritaires sur la table de l'application."""
    used = {int(sp["activity"]) for w in weeks for sp in w}
    if cloud:
        out = [
            {**a, "name": a.get("name") or ACTIVITY_NAMES.get(a["n"], f"Activité {a['n']}"), "origin": "thermostat"}
            for a in cloud
        ]
        known = {a["n"] for a in cloud}
        out += [
            {"n": n, "name": ACTIVITY_NAMES.get(n, f"Activité {n}"), "setpoint": None, "origin": None}
            for n in sorted(used - known)
        ]
        return out
    out = []
    for n in sorted(set(ACTIVITY_NAMES) | used | set(acts)):
        a = acts.get(n, {})
        out.append(
            {
                "n": n,
                "name": ACTIVITY_NAMES.get(n, f"Activité {n}"),
                "setpoint": a.get("setpoint"),
                "origin": a.get("origin"),
            }
        )
    return out


# Créneaux par jour, comme l'appli De Dietrich ; le cloud n'en documente pas la limite.
MAX_PER_DAY = 6
DAY_NAMES = ["dimanche", "lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi"]


def validate_week(week: list[dict[str, Any]], activities: set[int] | None = None) -> list[dict[str, Any]]:
    """Contrôle une semaine avant écriture et la rend triée, au format commun. Chauffage :
    activité connue du thermostat (`activities`) ; eau chaude (activities=None) : confort
    ou non. Lève ValueError avec un message lisible."""
    out, seen = [], set()
    for sp in week:
        day, time_ = sp.get("day"), str(sp.get("time") or "")
        if not isinstance(day, int) or not 0 <= day <= 6:
            raise ValueError(f"jour invalide : {day}")
        try:
            h, m = (int(x) for x in time_.split(":"))
        except ValueError:
            raise ValueError(f"heure invalide : {time_ or 'vide'}") from None
        if len(time_) != 5 or not (0 <= h <= 23 and 0 <= m <= 59):
            raise ValueError(f"heure invalide : {time_}")
        if (day, time_) in seen:
            raise ValueError(f"deux créneaux à {time_} le {DAY_NAMES[day]}")
        seen.add((day, time_))
        if activities is None:
            out.append({"day": day, "time": time_, "comfort": bool(sp.get("comfort"))})
        else:
            act = sp.get("activity")
            if act not in activities:
                raise ValueError(f"activité inconnue du thermostat : {act}")
            out.append({"day": day, "time": time_, "activity": act, "setpoint": sp.get("setpoint")})
    if not out:
        raise ValueError("le programme doit garder au moins un créneau")
    for day in range(7):
        if sum(1 for sp in out if sp["day"] == day) > MAX_PER_DAY:
            raise ValueError(f"{MAX_PER_DAY} créneaux au plus par jour ({DAY_NAMES[day]})")
    return sorted(out, key=lambda sp: (sp["day"], sp["time"]))


def changed_days(old: list[dict[str, Any]], new: list[dict[str, Any]]) -> list[int]:
    """Jours dont les créneaux diffèrent entre deux semaines."""

    def by_day(week: list[dict[str, Any]], day: int) -> list[tuple]:
        return sorted(tuple(sorted(sp.items())) for sp in week if sp["day"] == day)

    return [d for d in range(7) if by_day(old, d) != by_day(new, d)]


def days_label(days: list[int]) -> str:
    """« lundi, mardi », « toute la semaine », dans l'ordre lundi → dimanche."""
    if len(days) == 7:
        return "toute la semaine"
    return ", ".join(DAY_NAMES[d] for d in sorted(days, key=lambda d: (d + 6) % 7))
