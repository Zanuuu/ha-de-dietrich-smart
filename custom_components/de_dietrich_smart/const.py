"""Constantes de l'intégration (valeurs reprises de l'application ancienne application)."""

from datetime import timedelta
from typing import Final

DOMAIN: Final = "de_dietrich_smart"

# Relève du cloud : 60 s, doublée à chaque échec jusqu'à 15 min.
POLL_INTERVAL: Final = timedelta(seconds=60)
POLL_MAX: Final = timedelta(seconds=900)
# Relève anticipée après une commande.
REFRESH_SOON: Final = 6.0
# Fenêtre d'annulation d'une commande.
UNDO_WINDOW: Final = 120.0
# Dérogations de durée fixe (secondes) ; « next » et « permanent » n'ont pas d'échéance.
DURATIONS: Final = {"1h": 3600, "2h": 7200}
DURATION_CHOICES: Final = ("next", "1h", "2h", "permanent")
HOLIDAY_MAX_DAYS: Final = 365
# Pression basse : en dessous, ou si la chaudière la signale.
LOW_PRESSURE: Final = 1.0
# Plancher chauffant : départ maxi admissible et température extérieure de base (la ville).
DEFAULT_MAX_FLOOR_FLOW: Final = 45.0
DEFAULT_BASE_TEMP: Final = -15.0

# Options de l'entrée.
CONF_DEFAULT_DURATION: Final = "default_duration"
CONF_EMITTER: Final = "emitter"
CONF_CONNECTION: Final = "connection"
CONF_MAX_FLOOR_FLOW: Final = "max_floor_flow"
CONF_BASE_TEMP: Final = "base_temp"
CONF_WEATHER_ENTITY: Final = "weather_entity"
DEFAULT_OPTIONS: Final = {
    CONF_DEFAULT_DURATION: "next",
    CONF_EMITTER: "floor",
    CONF_CONNECTION: "direct",
    CONF_MAX_FLOOR_FLOW: DEFAULT_MAX_FLOOR_FLOW,
    CONF_BASE_TEMP: DEFAULT_BASE_TEMP,
}
