"""Commandes : exécution, journal, annulation et retours au programme différés.

Port de `controller.py` de l'application ancienne application : mêmes contrôles et messages.
Chaque commande garde l'état d'avant, ce qui permet « Annuler » pendant 2 min. Les
dérogations d'une durée fixe (1 h, 2 h) passent la zone en manuel puis la remettent au
programme à l'échéance, si personne n'a changé le réglage entre-temps.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, timedelta
from functools import partial
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from . import events
from .bdr import programs
from .bdr.cloud import BdrClient
from .bdr.models import Dhw, SourceError, State, Zone
from .const import DURATIONS, HOLIDAY_MAX_DAYS, UNDO_WINDOW
from .coordinator import DeDietrichCoordinator
from .events import DHW_MODE_FR, ZONE_MODE_FR, fmt_day, fmt_temp
from .journal import Journal

_LOGGER = logging.getLogger(__name__)
ORIGIN = events.APP_ORIGIN


class CommandError(Exception):
    """Commande refusée ou en échec ; message lisible par l'utilisateur."""


@dataclass
class CommandResult:
    command_id: int
    message: str
    undo_until: float | None


class CommandRunner:
    def __init__(
        self, hass: HomeAssistant, client: BdrClient, coordinator: DeDietrichCoordinator, journal: Journal
    ) -> None:
        self.hass = hass
        self.client = client
        self.coordinator = coordinator
        self.journal = journal
        self._lock = asyncio.Lock()

    # --- Accès à l'état courant -------------------------------------------

    def _state(self) -> State:
        if not self.coordinator.data:
            raise CommandError("aucune donnée de la chaudière pour l'instant, réessayez dans un instant")
        return self.coordinator.data

    def _zone(self) -> Zone:
        z = self._state().zone
        if not z:
            raise CommandError("aucune zone de chauffage connue")
        return z

    def _dhw(self) -> Dhw:
        d = self._state().dhw
        if not d:
            raise CommandError("aucune production d'eau chaude connue")
        return d

    @staticmethod
    def _zone_before(z: Zone) -> dict[str, Any]:
        return {"mode": z.mode, "setpoint": z.setpoint, "program": z.program, "fireplace": z.fireplace}

    @staticmethod
    def _dhw_before(d: Dhw) -> dict[str, Any]:
        return {"mode": d.mode, "comfort_setpoint": d.comfort_setpoint, "reduced_setpoint": d.reduced_setpoint}

    # --- Exécution commune ------------------------------------------------

    async def _run(
        self,
        action: str,
        params: dict[str, Any],
        before: dict[str, Any],
        coro_factory: Callable[[], Awaitable[None]],
        message: str,
        origin: str = ORIGIN,
        undo_of: int | None = None,
        undoable: bool = True,
    ) -> CommandResult:
        cid = self.journal.add_command(action, params, before, origin, undo_of)
        try:
            await coro_factory()
        except SourceError as exc:
            self.journal.finish_command(cid, False, str(exc))
            raise CommandError(f"la commande n'a pas abouti : {exc}") from exc
        self.journal.finish_command(cid, True)
        self.coordinator.async_refresh_soon()
        _LOGGER.info("commande %s %s (%s)", action, params, origin)
        undo_until = time.time() + UNDO_WINDOW if undo_of is None and undoable else None
        return CommandResult(cid, message, undo_until)

    # --- Zone de chauffage ------------------------------------------------

    async def set_setpoint(self, setpoint: float, duration: str) -> CommandResult:
        async with self._lock:
            z = self._zone()
            setpoint = round(setpoint * 2) / 2
            if not z.setpoint_min <= setpoint <= z.setpoint_max:
                raise CommandError(f"consigne hors limites ({fmt_temp(z.setpoint_min)} à {fmt_temp(z.setpoint_max)})")
            before = self._zone_before(z)
            factory: Callable[[], Awaitable[None]]
            if duration == "next":
                factory = lambda: self.client.zone_override(z.id, setpoint)  # noqa: E731
                mode, msg = "override", f"{fmt_temp(setpoint)} jusqu'au prochain créneau"
            elif duration == "permanent":
                factory = lambda: self.client.zone_manual(z.id, setpoint)  # noqa: E731
                mode, msg = "manual", f"{fmt_temp(setpoint)} en permanence"
            elif duration in DURATIONS:
                factory = lambda: self.client.zone_manual(z.id, setpoint)  # noqa: E731
                due = time.time() + DURATIONS[duration]
                hhmm = dt_util.as_local(dt_util.utc_from_timestamp(due)).strftime("%H:%M")
                mode, msg = "manual", f"{fmt_temp(setpoint)} jusqu'à {hhmm}"
            else:
                raise CommandError(f"durée inconnue : {duration}")
            self.journal.drop_pending(zone_id=z.id)
            res = await self._run("consigne", {"setpoint": setpoint, "duration": duration}, before, factory, msg)
            self.coordinator.expect(f"{z.id}:mode", mode)
            self.coordinator.expect(f"{z.id}:setpoint", setpoint)
            if duration in DURATIONS:
                self.journal.add_pending(
                    due,
                    "zone_schedule",
                    {"zone_id": z.id, "program": z.program or 1},
                    res.command_id,
                    {"mode": "manual", "setpoint": setpoint},
                )
            return res

    async def set_mode(self, mode: str) -> CommandResult:
        async with self._lock:
            z = self._zone()
            before = self._zone_before(z)
            factory: Callable[[], Awaitable[None]]
            if mode == "schedule":
                factory = lambda: self.client.zone_schedule(z.id, z.program or 1)  # noqa: E731
            elif mode == "antifrost":
                factory = lambda: self.client.zone_antifrost(z.id)  # noqa: E731
            else:
                raise CommandError(f"mode inconnu : {mode}")
            self.journal.drop_pending(zone_id=z.id)
            res = await self._run("mode", {"mode": mode}, before, factory, f"Mode {ZONE_MODE_FR[mode].lower()}")
            self.coordinator.expect(f"{z.id}:mode", mode)
            return res

    async def set_program(self, program: int) -> CommandResult:
        async with self._lock:
            z = self._zone()
            if program not in z.programs:
                raise CommandError(f"programme inconnu : {program}")
            before = self._zone_before(z)
            self.journal.drop_pending(zone_id=z.id)
            res = await self._run(
                "programme",
                {"program": program},
                before,
                lambda: self.client.zone_program(z.id, program),
                f"Programme {program} actif",
            )
            self.coordinator.expect(f"{z.id}:program", program)
            self.coordinator.expect(f"{z.id}:mode", "schedule")
            return res

    async def set_fireplace(self, active: bool) -> CommandResult:
        async with self._lock:
            z = self._zone()
            if not z.fireplace_capable:
                raise CommandError("le mode cheminée n'est pas disponible sur ce thermostat")
            res = await self._run(
                "cheminee",
                {"active": active},
                self._zone_before(z),
                lambda: self.client.zone_fireplace(z.id, active),
                "Mode cheminée activé" if active else "Mode cheminée désactivé",
            )
            self.coordinator.expect(f"{z.id}:fireplace", active)
            return res

    # --- Eau chaude -------------------------------------------------------

    async def set_dhw_mode(self, mode: str) -> CommandResult:
        async with self._lock:
            d = self._dhw()
            if mode not in ("schedule", "comfort", "eco"):
                raise CommandError(f"mode d'eau chaude inconnu : {mode}")
            res = await self._run(
                "ecs_mode",
                {"mode": mode},
                self._dhw_before(d),
                lambda: self.client.dhw_mode(d.id, mode),
                f"Eau chaude : {DHW_MODE_FR[mode].lower()}",
            )
            self.coordinator.expect(f"{d.id}:mode", mode)
            return res

    async def set_dhw_setpoint(self, kind: str, value: float) -> CommandResult:
        async with self._lock:
            d = self._dhw()
            value = round(value * 2) / 2
            factory: Callable[[], Awaitable[None]]
            if kind == "comfort":
                lo, hi, attr = d.comfort_min, d.comfort_max, "comfort_setpoint"
                factory = lambda: self.client.dhw_comfort_setpoint(d.id, value)  # noqa: E731
            elif kind == "reduced":
                lo, hi, attr = d.reduced_min, d.reduced_max, "reduced_setpoint"
                factory = lambda: self.client.dhw_reduced_setpoint(d.id, value)  # noqa: E731
            else:
                raise CommandError(f"consigne d'eau chaude inconnue : {kind}")
            if (lo is not None and value < lo) or (hi is not None and value > hi):
                raise CommandError(f"consigne hors limites ({fmt_temp(lo)} à {fmt_temp(hi)})")
            label = "confort" if kind == "comfort" else "réduite"
            res = await self._run(
                "ecs_consigne",
                {"kind": kind, "value": value},
                self._dhw_before(d),
                factory,
                f"Eau chaude {label} : {fmt_temp(value)}",
            )
            self.coordinator.expect(f"{d.id}:{attr}", value)
            return res

    async def dhw_boost(self) -> CommandResult:
        async with self._lock:
            d = self._dhw()
            if not d.boost_capable:
                raise CommandError("le boost d'eau chaude n'est pas disponible sur cette chaudière")
            if d.mode == "boost":
                raise CommandError("boost déjà en cours")
            if d.mode != "schedule":
                raise CommandError(
                    "le boost n'est accepté qu'en mode programme : passez d'abord l'eau chaude en Programme"
                )
            res = await self._run(
                "ecs_boost",
                {},
                self._dhw_before(d),
                lambda: self.client.dhw_boost(d.id),
                f"Boost eau chaude : {fmt_temp(d.comfort_setpoint)} pendant 30 min environ",
                undoable=False,
            )
            self.coordinator.expect(f"{d.id}:mode", "boost")
            return res

    # --- Activités et courbe de chauffe -----------------------------------

    async def set_activity(self, n: int, setpoint: float) -> CommandResult:
        """Consigne d'une activité, écrite sur le thermostat par le cloud."""
        async with self._lock:
            z = self._zone()
            act = next((a for a in z.activities if a["n"] == n), None)
            if not act:
                raise CommandError(f"activité {n} inconnue du thermostat")
            step = act.get("step") or 0.5
            setpoint = round(round(setpoint / step) * step, 1)
            if not act["min"] <= setpoint <= act["max"]:
                raise CommandError(f"consigne hors limites ({fmt_temp(act['min'])} à {fmt_temp(act['max'])})")
            name = act.get("name") or programs.ACTIVITY_NAMES.get(n, f"Activité {n}")
            res = await self._run(
                "activite",
                {"activity": n, "setpoint": setpoint, "name": name},
                {"setpoint": act.get("setpoint")},
                lambda: self.client.zone_activities(z.id, {n: setpoint}),
                f"Activité {name} : {fmt_temp(setpoint)}",
            )
            self.coordinator.expect(f"{z.id}:activity:{n}", setpoint)
            return res

    async def set_heating_curve(self, slope: float | None, base: float | None) -> CommandResult:
        async with self._lock:
            z = self._zone()
            c = z.heating_curve
            if not c:
                raise CommandError("la courbe de chauffe n'est pas disponible pour cette zone")
            new_slope = c["slope"] if slope is None else round(slope, 2)
            new_base = c["base"] if base is None else round(base, 1)
            for v, lo, hi, label in (
                (new_slope, c.get("slope_min"), c.get("slope_max"), "pente"),
                (new_base, c.get("base_min"), c.get("base_max"), "pied de courbe"),
            ):
                if (lo is not None and v < lo) or (hi is not None and v > hi):
                    raise CommandError(f"{label} hors limites ({lo} à {hi})".replace(".", ","))
            res = await self._run(
                "courbe",
                {"slope": new_slope, "base": new_base},
                {"slope": c["slope"], "base": c["base"]},
                lambda: self.client.zone_heating_curve(z.id, new_slope, new_base),
                f"Courbe de chauffe : pente {new_slope}, pied {fmt_temp(new_base)}".replace(".", ","),
            )
            self.coordinator.expect(f"{z.id}:curve", (new_slope, new_base))
            return res

    # --- Créneaux des programmes --------------------------------------------

    async def set_week(self, program: int, week: list[dict[str, Any]]) -> CommandResult:
        """Créneaux d'un programme de chauffage, remplacés en entier."""
        async with self._lock:
            z = self._zone()
            old = z.week.get(program)
            if program not in z.programs or old is None:
                raise CommandError(f"programme {program} pas encore lu, réessayez dans un instant")
            known = {a["n"] for a in z.activities} or set(programs.ACTIVITY_NAMES)
            try:
                week = programs.validate_week(week, known)
            except ValueError as exc:
                raise CommandError(str(exc)) from exc
            days = programs.changed_days(old, week)
            if not days:
                raise CommandError("aucun créneau modifié")
            res = await self._run(
                "creneaux",
                {"program": program, "days": days},
                {"week": old},
                lambda: self.client.zone_week(z.id, program, week),
                f"Programme {program} modifié : {programs.days_label(days)}",
            )
            self.coordinator.expect(f"{z.id}:week:{program}", week)
            return res

    async def set_dhw_week(self, week: list[dict[str, Any]]) -> CommandResult:
        """Créneaux confort de l'eau chaude (programme unique), remplacés en entier."""
        async with self._lock:
            d = self._dhw()
            if not d.week:
                raise CommandError("programme d'eau chaude pas encore lu, réessayez dans un instant")
            try:
                week = programs.validate_week(week)
            except ValueError as exc:
                raise CommandError(str(exc)) from exc
            days = programs.changed_days(d.week, week)
            if not days:
                raise CommandError("aucun créneau modifié")
            res = await self._run(
                "ecs_creneaux",
                {"days": days},
                {"week": d.week},
                lambda: self.client.dhw_week(d.id, week),
                f"Programme eau chaude modifié : {programs.days_label(days)}",
            )
            self.coordinator.expect(f"{d.id}:week", week)
            return res

    # --- Vacances ---------------------------------------------------------

    async def set_holiday(self, start: date | None, end: date | None) -> CommandResult:
        """Période de vacances (jours entiers) ; start et end à None l'annulent."""
        async with self._lock:
            a = self._state().appliance
            if not a:
                raise CommandError("aucune chaudière connue")
            before = {"start": a.holiday_start, "end": a.holiday_end}
            if start is None or end is None:
                if not a.holiday_start:
                    raise CommandError("aucune période de vacances à annuler")
                msg = "Vacances annulées"
            else:
                today = dt_util.now().date()
                # Le cloud termine la période à 0 h le jour de fin : c'est le jour du retour.
                if end <= start:
                    raise CommandError("le retour doit suivre le départ d'au moins un jour")
                if end <= today:
                    raise CommandError("la période est déjà passée")
                if end - start > timedelta(days=HOLIDAY_MAX_DAYS):
                    raise CommandError("période de vacances limitée à un an")
                msg = f"Vacances : départ le {fmt_day(start.isoformat())}, retour le {fmt_day(end.isoformat())}"
            res = await self._run(
                "vacances",
                {"start": start and start.isoformat(), "end": end and end.isoformat()},
                before,
                lambda: self.client.holiday(a.id, start, end),
                msg,
            )
            self.coordinator.expect(f"{a.id}:holiday", (start and start.isoformat(), end and end.isoformat()))
            return res

    # --- Annulation -------------------------------------------------------

    async def undo(self, cid: int) -> CommandResult:
        async with self._lock:
            cmd = self.journal.command(cid)
            if not cmd or not cmd["ok"]:
                raise CommandError("commande introuvable ou déjà en échec")
            if cmd["undone"] or cmd["undo_of"]:
                raise CommandError("cette commande ne peut plus être annulée")
            if time.time() - cmd["ts"] > UNDO_WINDOW:
                raise CommandError("délai d'annulation dépassé")
            latest = self.journal.commands(1)
            if latest and latest[0]["id"] != cid:
                raise CommandError("une autre commande a suivi, annulation impossible")
            b, action = cmd["before"], cmd["action"]
            factory: Callable[[], Awaitable[None]]
            if action in ("consigne", "mode", "programme", "cheminee"):
                z = self._zone()
                self.journal.drop_pending(zone_id=z.id)

                async def restore_zone() -> None:
                    if action == "cheminee":
                        await self.client.zone_fireplace(z.id, bool(b.get("fireplace")))
                        return
                    # Programme changé par la commande : z (dernière relève) peut encore donner
                    # l'ancien programme si l'annulation précède la relève suivante.
                    if b.get("program") and (action == "programme" or b["program"] != z.program):
                        await self.client.zone_program(z.id, b["program"])
                    m, sp = b.get("mode"), b.get("setpoint")
                    if m == "manual" and sp is not None:
                        await self.client.zone_manual(z.id, sp)
                    elif m == "override" and sp is not None:
                        await self.client.zone_override(z.id, sp)
                    elif m == "antifrost":
                        await self.client.zone_antifrost(z.id)
                    else:
                        await self.client.zone_schedule(z.id, b.get("program") or z.program or 1)

                factory = restore_zone
                if b.get("mode"):
                    self.coordinator.expect(f"{z.id}:mode", b["mode"])
            elif action in ("ecs_mode", "ecs_consigne"):
                d = self._dhw()

                async def restore_dhw() -> None:
                    if action == "ecs_mode" and b.get("mode") in ("schedule", "comfort", "eco"):
                        await self.client.dhw_mode(d.id, b["mode"])
                    elif action == "ecs_consigne":
                        kind = cmd["params"]["kind"]
                        old = b.get("comfort_setpoint" if kind == "comfort" else "reduced_setpoint")
                        if old is not None:
                            if kind == "comfort":
                                await self.client.dhw_comfort_setpoint(d.id, old)
                            else:
                                await self.client.dhw_reduced_setpoint(d.id, old)

                factory = restore_dhw
            elif action == "activite":
                z = self._zone()
                n, old = cmd["params"]["activity"], b.get("setpoint")
                if old is None:
                    raise CommandError("consigne précédente inconnue, annulation impossible")
                factory = lambda: self.client.zone_activities(z.id, {n: old})  # noqa: E731
            elif action == "courbe":
                z = self._zone()
                factory = lambda: self.client.zone_heating_curve(z.id, b["slope"], b["base"])  # noqa: E731
            elif action == "creneaux":
                z, n, old_week = self._zone(), cmd["params"]["program"], b["week"]
                factory = lambda: self.client.zone_week(z.id, n, old_week)  # noqa: E731
                self.coordinator.expect(f"{z.id}:week:{n}", old_week)
            elif action == "ecs_creneaux":
                d, old_week = self._dhw(), b["week"]
                factory = lambda: self.client.dhw_week(d.id, old_week)  # noqa: E731
                self.coordinator.expect(f"{d.id}:week", old_week)
            elif action == "vacances":
                a = self._state().appliance
                assert a is not None
                start = date.fromisoformat(b["start"]) if b.get("start") else None
                end = date.fromisoformat(b["end"]) if b.get("end") else None
                factory = lambda: self.client.holiday(a.id, start, end)  # noqa: E731
            else:
                raise CommandError("cette commande ne peut pas être annulée")
            res = await self._run(
                "annulation", {"command_id": cid}, {}, factory, "Réglage précédent rétabli", undo_of=cid
            )
            self.journal.mark_undone(cid)
            return res

    def last_undoable(self) -> int | None:
        """Dernière commande encore annulable, s'il y en a une."""
        latest = self.journal.commands(1)
        if not latest:
            return None
        c = latest[0]
        if c["ok"] and not c["undone"] and not c["undo_of"] and time.time() - c["ts"] <= UNDO_WINDOW:
            return int(c["id"])
        return None

    # --- Retours au programme différés ------------------------------------

    async def async_run_pending(self) -> None:
        now = time.time()
        state = self.coordinator.data
        z = state.zone if state else None
        for p in self.journal.pendings():
            if p["due"] > now:
                continue
            self.journal.drop_pending(p["id"])
            exp = p["expect"]
            unchanged = (
                z is not None
                and z.id == p["params"].get("zone_id")
                and z.mode == exp.get("mode")
                and z.setpoint is not None
                and abs(z.setpoint - exp.get("setpoint", -100)) < 0.05
            )
            if not unchanged or z is None:
                msg = "Retour au programme annulé : le réglage a changé entre-temps"
                self.journal.add_event(events.event("programme", msg, "info"))
                continue
            zone, program = z, int(p["params"].get("program") or 1)
            try:
                await self._run(
                    "mode",
                    {"mode": "schedule"},
                    self._zone_before(zone),
                    partial(self.client.zone_schedule, zone.id, program),
                    "Retour au programme",
                    origin="retour programmé",
                )
                self.coordinator.expect(f"{zone.id}:mode", "schedule")
            except CommandError as exc:
                self.journal.add_event(events.event("programme", f"Retour au programme en échec : {exc}", "warning"))
