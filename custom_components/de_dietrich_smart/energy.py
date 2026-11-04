"""Consommation de gaz : relève horaire, conservation des heures, statistiques du tableau Énergie.

Le cloud donne les jours en UTC (horodatés à minuit Z) sur plus d'un an, et les heures sur une
quinzaine de jours seulement : les heures sont conservées dans un fichier `.storage`. Chaque
import réécrit toute la série et recalcule la somme cumulée depuis le début : jamais
d'addition incrémentale, donc jamais de double compte (bogue de la MR !50 de l'application).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Literal
from zoneinfo import ZoneInfo

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
from homeassistant.util import slugify

from .bdr.cloud import BdrClient
from .bdr.models import AuthError, DayConsumption, HourConsumption, SourceError
from .const import CONF_GAS_PRICE, DOMAIN

if TYPE_CHECKING:
    from homeassistant.components.recorder.models import StatisticData
    from homeassistant.config_entries import ConfigEntry

    from .coordinator import DeDietrichCoordinator

_LOGGER = logging.getLogger(__name__)
Kind = Literal["heating", "dhw"]
FIRST_DAYS = 400
FIRST_HOURS_DAYS = 16
NEXT_DAYS = 2


def _value(c: DayConsumption | HourConsumption, kind: Kind) -> float | None:
    return c.heating_kwh if kind == "heating" else c.dhw_kwh


def build_statistics(
    days: list[DayConsumption], hours: list[HourConsumption], kind: Kind, now: datetime
) -> list[StatisticData]:
    """Série horaire (UTC) et somme cumulée. Un jour UTC prend ses heures s'il les a toutes,
    ou si le cloud ne donne pas (encore) sa valeur ; sinon la valeur du jour, à minuit UTC."""
    past_hours = [h for h in hours if h.ts <= now]
    by_day: dict[date, list[HourConsumption]] = {}
    for h in past_hours:
        by_day.setdefault(h.ts.astimezone(UTC).date(), []).append(h)
    daily = {d.day: d for d in days}
    today = now.astimezone(UTC).date()
    points: dict[datetime, float] = {}
    for day in sorted(set(daily) | set(by_day)):
        hs = by_day.get(day, [])
        if hs and (day not in daily or len(hs) >= 24 or day >= today):
            for h in hs:
                if (v := _value(h, kind)) is not None:
                    points[h.ts.astimezone(UTC)] = v
        elif day in daily and (v := _value(daily[day], kind)) is not None and day <= today:
            # Les 24 heures du jour sont écrites (valeur à minuit, 0 ensuite) : elles remplacent
            # d'éventuelles heures déjà importées, qui laisseraient sinon des sommes périmées.
            midnight = datetime.combine(day, datetime.min.time(), UTC)
            for n in range(24):
                points[midnight + timedelta(hours=n)] = v if n == 0 else 0.0
    out: list[StatisticData] = []
    total = 0.0
    for start in sorted(points):
        total += points[start]
        out.append({"start": start, "state": points[start], "sum": round(total, 3)})
    return out


def build_cost_statistics(stats: list[StatisticData], price: float) -> list[StatisticData]:
    """Coût de chaque heure au prix fixe, même cumul : tout l'historique suit le prix du moment."""
    return [
        {"start": s["start"], "state": round(s["state"] * price, 4), "sum": round(s["sum"] * price, 4)} for s in stats
    ]


def today_kwh(hours: list[HourConsumption], now: datetime, time_zone: str) -> float | None:
    """Chauffage + eau chaude depuis minuit (heure locale)."""
    tz = ZoneInfo(time_zone)
    today = now.astimezone(tz).date()
    vals = [
        (h.heating_kwh or 0.0) + (h.dhw_kwh or 0.0)
        for h in hours
        if h.ts <= now and h.ts.astimezone(tz).date() == today
    ]
    return round(sum(vals), 1) if vals else None


@dataclass
class ConsumptionData:
    days: list[DayConsumption]
    hours: list[HourConsumption]
    today_kwh: float | None


class ConsumptionCoordinator(DataUpdateCoordinator[ConsumptionData]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: BdrClient, main: DeDietrichCoordinator) -> None:
        super().__init__(
            hass, _LOGGER, config_entry=entry, name=f"{DOMAIN}_consumption", update_interval=timedelta(hours=1)
        )
        self.client = client
        self.main = main
        self._store: Store[dict] = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.consumption")
        self._days: dict[date, DayConsumption] = {}
        self._hours: dict[datetime, HourConsumption] = {}

    async def async_load(self) -> None:
        """Relit les jours et les heures conservés (le cloud ne garde qu'une quinzaine de jours d'heures)."""
        raw = await self._store.async_load() or {}
        for k, (h, d) in (raw.get("days") or {}).items():
            self._days[date.fromisoformat(k)] = DayConsumption(date.fromisoformat(k), h, d)
        for k, (h, d) in (raw.get("hours") or {}).items():
            ts = datetime.fromisoformat(k)
            self._hours[ts] = HourConsumption(ts, h, d)

    def _dump(self) -> dict:
        return {
            "days": {k.isoformat(): [v.heating_kwh, v.dhw_kwh] for k, v in sorted(self._days.items())},
            "hours": {k.isoformat(): [v.heating_kwh, v.dhw_kwh] for k, v in sorted(self._hours.items())},
        }

    async def async_save(self) -> None:
        await self._store.async_save(self._dump())

    async def _async_update_data(self) -> ConsumptionData:
        a = self.main.data.appliance if self.main.data else None
        if not a:
            raise UpdateFailed("aucune chaudière connue")
        now = dt_util.utcnow()
        today = dt_util.now().date()
        day_start = today - timedelta(days=FIRST_DAYS if not self._days else NEXT_DAYS)
        hour_start = today - timedelta(days=FIRST_HOURS_DAYS if not self._hours else NEXT_DAYS)
        try:
            days = await self.client.fetch_consumption_days(a.id, day_start, today)
            hours = await self.client.fetch_consumption_hours(a.id, hour_start, today)
        except AuthError as exc:
            # Réauthentification : plus de relève tant que l'entrée n'est pas rechargée.
            raise ConfigEntryAuthFailed(str(exc)) from exc
        except SourceError as exc:
            raise UpdateFailed(f"consommation indisponible : {exc}") from exc
        self._days.update({d.day: d for d in days})
        self._hours.update({h.ts.astimezone(UTC): h for h in hours})
        self._store.async_delay_save(self._dump, 10)
        all_days = [self._days[k] for k in sorted(self._days)]
        all_hours = [self._hours[k] for k in sorted(self._hours)]
        self._import(a.id, all_days, all_hours, now)
        return ConsumptionData(all_days, all_hours, today_kwh(all_hours, now, self.hass.config.time_zone))

    def _import(
        self, appliance_id: str, days: list[DayConsumption], hours: list[HourConsumption], now: datetime
    ) -> None:
        if "recorder" not in self.hass.config.components:
            return
        from homeassistant.components.recorder.models import StatisticMeanType
        from homeassistant.components.recorder.statistics import async_add_external_statistics

        price = self.config_entry.options.get(CONF_GAS_PRICE) if self.config_entry else None
        for kind, label in (("heating", "Gaz chauffage"), ("dhw", "Gaz eau chaude")):
            stats = build_statistics(days, hours, kind, now)  # type: ignore[arg-type]
            if not stats:
                continue
            statistic_id = f"{DOMAIN}:{slugify(appliance_id)}_gas_{kind}"
            async_add_external_statistics(
                self.hass,
                {
                    "source": DOMAIN,
                    "statistic_id": statistic_id,
                    "name": label,
                    "unit_of_measurement": "kWh",
                    "unit_class": "energy",
                    "has_sum": True,
                    "mean_type": StatisticMeanType.NONE,
                },
                stats,
            )
            if price:
                # Le tableau Énergie n'applique pas de prix à une statistique externe : il lui faut le coût.
                async_add_external_statistics(
                    self.hass,
                    {
                        "source": DOMAIN,
                        "statistic_id": f"{statistic_id}_cost",
                        "name": f"Coût {label[0].lower()}{label[1:]}",
                        "unit_of_measurement": self.hass.config.currency,
                        "unit_class": None,
                        "has_sum": True,
                        "mean_type": StatisticMeanType.NONE,
                    },
                    build_cost_statistics(stats, float(price)),
                )
