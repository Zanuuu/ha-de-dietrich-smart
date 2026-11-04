"""Source « cloud BDR Thermea » : l'API des applis De Dietrich SMART TC° et Remeha Home.

API non officielle, relevée par le projet communautaire msvisser/remeha_home
(documentation/api.md, et sa PR 122 pour activités, courbe de chauffe, boost et
vacances, mesurés sur une installation réelle) ; liste des adresses de l'appli dans
SimoneAvogadro/De-Dietrich-API. Tout ce qui dépend de son format est isolé ici.

Heures : la plupart sont locales suivies d'un « Z » (changements de programme, dates de
vacances) ; la fin d'une dérogation et celle d'un boost sont de vrais instants UTC.

Authentification Azure AD B2C (code + PKCE) avec les identifiants de l'appli. Les
jetons restent en mémoire : un redémarrage refait une connexion. Le client ID et la
clé d'abonnement sont ceux de l'appli mobile, publics dans le code de remeha_home.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import re
import secrets
import time
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from datetime import time as dtime
from typing import Any
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import httpx

from .models import (
    Appliance,
    AuthError,
    DayConsumption,
    Dhw,
    HourConsumption,
    SourceError,
    State,
    Zone,
)

log = logging.getLogger(__name__)

LOGIN = "https://remehalogin.bdrthermea.net/bdrb2cprod.onmicrosoft.com"
# La casse diffère selon l'étape (« V3.1 » ou « v3.1 ») : reprise telle quelle.
POLICY_AUTH = "B2C_1A_RPSignUpSignInNewRoomV3.1"
POLICY_FORM = "B2C_1A_RPSignUpSignInNewRoomv3.1"
CLIENT_ID = "6ce007c6-0628-419e-88f4-bee2e6418eec"
REDIRECT_URI = "com.b2c.remehaapp://login-callback"
SCOPE = "openid https://bdrb2cprod.onmicrosoft.com/iotdevice/user_impersonation offline_access"
API = "https://api.bdrthermea.net/Mobile/api"
SUBSCRIPTION_KEY = "df605c5470d846fc91e848b1cc653ddf"
USER_AGENT = "ha-de-dietrich-smart"

ZONE_MODE = {
    "Scheduling": "schedule",
    "TemporaryOverride": "override",
    "Manual": "manual",
    "FrostProtection": "antifrost",
}
DHW_MODE_ENDPOINT = {
    "schedule": "schedule",
    "comfort": "continuous-comfort",
    "eco": "anti-frost",
}
# Noms génériques renvoyés en anglais par l'API.
_ZONE_NAMES = {"DHW": "Eau chaude", "CH": "Chauffage"}
# Statuts « sans défaut » connus ; tout autre statut est signalé comme défaut.
NORMAL_ERROR_STATUS = {"Running", "Standby", "NoError", "None", "Idle"}
PARIS = ZoneInfo("Europe/Paris")
# Programmes horaires (4 requêtes) : relus toutes les heures, pour suivre une modification
# faite sur le thermostat ou dans l'appli De Dietrich.
PROGRAMS_EVERY = 3600
# Erreurs actives : relues toutes les 10 min, à chaque relève pendant un défaut.
ERRORS_EVERY = 600
# Adresse refusée (4xx) : pas de nouvel essai avant 24 h.
UNSUPPORTED_RETRY = 24 * 3600
# Vacances annulées : le cloud exige un début antérieur à la fin.
HOLIDAY_NONE = ("0001-01-01T00:00:00Z", "0001-01-01T00:00:01Z")


def _ts(value: Any) -> datetime | None:
    """Horodatage ISO 8601 ; la date vide de l'API (0001-01-01) vaut None."""
    if not value or not isinstance(value, str) or value.startswith("0001-01-01"):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _ts_local(value: Any, tz: ZoneInfo) -> datetime | None:
    """Heure de changement du programme : le cloud donne l'heure locale suivie d'un « Z »
    trompeur (« 22:00:00Z » pour un changement à 22:00 à la ville, en hiver comme en été)."""
    dt = _ts(value)
    return dt.replace(tzinfo=tz) if dt else None


def _day(value: Any) -> str | None:
    """Date des vacances : jour entier, stocké « AAAA-MM-JJT00:00:01Z » (heure locale)."""
    dt = _ts(value)
    return dt.date().isoformat() if dt else None


def _first(d: dict[str, Any], *keys: str) -> Any:
    for k in keys:
        if d.get(k) not in (None, ""):
            return d[k]
    return None


# Noms d'usine des activités, renvoyés en anglais quelle que soit la langue du thermostat.
# Un nom choisi par l'utilisateur dans l'appli du fabricant est gardé tel quel.
ACTIVITY_NAMES_FR = {"sleep": "Nuit", "home": "Maison", "away": "Absent", "morning": "Matin", "evening": "Soir"}


def _activity_name(name: Any) -> str | None:
    if not isinstance(name, str) or not name.strip():
        return None
    return ACTIVITY_NAMES_FR.get(name.strip().lower(), name)


def parse_activities(data: Any) -> list[dict[str, Any]]:
    """Activités de chauffage (GET /climate-zones/{id}/activities). Les activités de
    rafraîchissement portent les mêmes numéros : seules celles de type Heating sont gardées."""
    out = []
    for a in data if isinstance(data, list) else []:
        if not isinstance(a, dict) or (a.get("type") not in (None, "Heating")):
            continue
        try:
            n = int(a["activityNumber"])
        except KeyError, TypeError, ValueError:
            continue
        out.append(
            {
                "n": n,
                "name": _activity_name(a.get("name")),
                "setpoint": _num(a.get("temperature")),
                "min": _num(a.get("setPointMin")) or 5.0,
                "max": _num(a.get("setPointMax")) or 30.0,
                "step": _num(a.get("increment")) or 0.5,
            }
        )
    return sorted(out, key=lambda a: a["n"])


def parse_heating_curve(data: Any) -> dict[str, Any] | None:
    """Courbe de chauffe (GET /climate-zones/{id}/heating-curve) ; None si absente."""
    if not isinstance(data, dict) or _num(data.get("slope")) is None:
        return None
    g = lambda k: _num(data.get(k))  # noqa: E731
    return {
        "slope": g("slope"),
        "slope_min": g("minimumSlope"),
        "slope_max": g("maximumSlope"),
        "slope_step": g("incrementSlope") or 0.1,
        "base": g("baseSetpoint"),
        "base_min": g("minimumBaseSetpoint"),
        "base_max": g("maximumBaseSetpoint"),
        "base_step": g("incrementBaseSetpoint") or 0.5,
        "max_flow": g("maxFlowTemperature"),
    }


def parse_active_errors(data: Any) -> list[dict[str, Any]]:
    """Erreurs actives (GET /appliances/{id}/activeerrors). Format non documenté : liste
    d'objets, à la racine ou sous une clé ; code et libellé cherchés sous plusieurs noms."""
    items = data
    if isinstance(data, dict):
        items = _first(data, "activeErrors", "errors", "items", "data") or []
    out = []
    for e in items if isinstance(items, list) else []:
        if isinstance(e, str):
            out.append({"code": e, "text": None, "raw": e})
        elif isinstance(e, dict):
            code = _first(e, "displayCode", "errorCode", "code", "faultCode", "errorCodeDisplay")
            text = _first(e, "description", "errorDescription", "message", "name", "title", "errorType")
            out.append({"code": None if code is None else str(code), "text": text, "raw": anonymize(e)})
    return out


def parse_week(data: Any) -> list[dict[str, Any]]:
    """Changements d'un programme au format commun. Chauffage (time-programs/heating/{n}) :
    activité et consigne propre ; eau chaude (time-programs/1) : confort ou non."""
    out = []
    for sp in (data or {}).get("switchPoints") or []:
        try:
            day, time_ = int(sp["day"]), str(sp["time"])[:5]
            if "comfortActive" in sp and "activity" not in sp:
                item: dict[str, Any] = {"comfort": bool(sp["comfortActive"])}
            else:
                item = {"activity": int(sp["activity"]), "setpoint": _num(sp.get("customValue"))}
        except KeyError, TypeError, ValueError:
            continue
        if 0 <= day <= 6 and re.fullmatch(r"\d{2}:\d{2}", time_):
            out.append({"day": day, "time": time_, **item})
    return out


def build_switch_points(week: list[dict[str, Any]]) -> dict[str, Any]:
    """Corps d'écriture d'un programme, inverse de parse_week : le cloud remplace le
    programme entier par ces changements."""
    points = []
    for sp in sorted(week, key=lambda x: (x["day"], x["time"])):
        if "comfort" in sp:
            points.append({"day": sp["day"], "time": sp["time"], "comfortActive": bool(sp["comfort"])})
        else:
            points.append(
                {"day": sp["day"], "time": sp["time"], "activity": sp["activity"], "customValue": sp.get("setpoint")}
            )
    return {"switchPoints": points}


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except TypeError, ValueError:
        return None


def _demand(value: Any) -> str | None:
    if value in ("ProducingHeat", "RequestingHeat"):
        return "heating"
    if value == "Idle":
        return "idle"
    return None


def _activity(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    v = value.lower()
    if "water" in v or "dhw" in v:
        return "dhw"
    if "heat" in v:
        return "heating"
    if v in ("idle", "standby", "none", "off"):
        return "idle"
    return None


def _dhw_mode(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    v = value.lower()
    if "boost" in v:
        return "boost"
    if "comfort" in v:
        return "comfort"
    if "schedul" in v:
        return "schedule"
    # La commande anti-frost (« Éco » ici) se relit « Off » (constaté le 02/11/2026).
    if "frost" in v or "eco" in v or "reduced" in v or v == "off":
        return "eco"
    return None


def parse_dashboard(
    dashboard: dict[str, Any],
    technical: dict[str, dict[str, Any]] | None = None,
    programs: dict[str, Any] | None = None,
    tz: ZoneInfo = PARIS,
    extras: dict[str, dict[str, Any]] | None = None,
    now: datetime | None = None,
    stopped_boosts: set[str] | None = None,
) -> list[Appliance]:
    """Convertit la réponse de /homes/dashboard dans le modèle commun. `programs` : par
    identifiant de zone, les programmes lus (chauffage : numéro → changements ; eau
    chaude : changements du programme actif). `extras` : par identifiant de zone ou
    d'appareil, les lectures complémentaires (activités, courbe de chauffe, erreurs).
    `stopped_boosts` : heures de fin (brutes) de boosts arrêtés par un changement de mode,
    que le cloud continue d'annoncer."""
    technical = technical or {}
    programs = programs or {}
    extras = extras or {}
    appliances = []
    for a in dashboard.get("appliances") or []:
        aid = str(a.get("applianceId", ""))
        tech = technical.get(aid) or {}
        gateways = tech.get("internetConnectedGateways") or []
        gw = gateways[0] if gateways else {}
        oti = a.get("outdoorTemperatureInformation") or {}
        outdoor = _num(oti.get("applianceOutdoorTemperature"))
        outdoor_source: str | None = "sonde"
        if outdoor is None:
            outdoor = _num(oti.get("cloudOutdoorTemperature"))
            if outdoor is None:
                outdoor = _num(oti.get("internetOutdoorTemperature"))
            outdoor_source = "météo" if outdoor is not None else None
        error_status = a.get("errorStatus")
        holiday = a.get("holidaySchedule") or {}
        app = Appliance(
            id=aid,
            name=tech.get("applianceName") or a.get("houseName") or "Chaudière",
            online=a.get("applianceOnline"),
            error_status=error_status,
            in_error=bool(error_status) and error_status not in NORMAL_ERROR_STATUS,
            pressure=_num(a.get("waterPressure")),
            pressure_ok=a.get("waterPressureOK"),
            outdoor_temp=outdoor,
            outdoor_source=outdoor_source,
            activity=_activity(a.get("activeThermalMode")),
            operating_mode=a.get("operatingMode"),
            gateway={k: gw.get(k) for k in ("name", "hardwareVersion", "softwareVersion") if gw.get(k) is not None},
            holiday_active=holiday.get("active"),
            holiday_start=_day(holiday.get("startTime")),
            holiday_end=_day(holiday.get("endTime")),
            seasonal_mode=a.get("seasonalMode"),
            active_errors=(extras.get(aid) or {}).get("active_errors"),
        )
        for z in a.get("climateZones") or []:
            zx = extras.get(str(z.get("climateZoneId", ""))) or {}
            preheat = z.get("preHeat") or {}
            app.zones.append(
                Zone(
                    id=str(z.get("climateZoneId", "")),
                    name=z.get("name") or "Zone",
                    room_temp=_num(z.get("roomTemperature")),
                    setpoint=_num(z.get("setPoint")),
                    setpoint_min=_num(z.get("setPointMin")) or 5.0,
                    setpoint_max=_num(z.get("setPointMax")) or 30.0,
                    mode=ZONE_MODE.get(z.get("zoneMode")),
                    # Vrai instant UTC, contrairement à nextSwitchTime (mesure de remeha_home).
                    override_end=_ts((z.get("temporaryOverride") or {}).get("endTime")),
                    schedule_setpoint=_num(z.get("currentScheduleSetPoint")),
                    next_setpoint=_num(z.get("nextSetpoint")),
                    next_switch=_ts_local(z.get("nextSwitchTime"), tz),
                    program=z.get("activeHeatingClimateTimeProgramNumber"),
                    demand=_demand(z.get("activeComfortDemand")),
                    fireplace=z.get("firePlaceModeActive"),
                    fireplace_capable=bool(z.get("capabilityFirePlaceMode")),
                    week=dict(programs.get(str(z.get("climateZoneId", ""))) or {}),
                    activities=list(zx.get("activities") or []),
                    heating_curve=zx.get("heating_curve"),
                    preheat_enabled=preheat.get("enabled"),
                    preheat_active=preheat.get("active"),
                )
            )
        for h in a.get("hotWaterZones") or []:
            ranges = h.get("setPointRanges") or {}
            # Pendant un boost, le cloud garde dhwZoneMode « Scheduling » : le boost se lit à
            # son heure de fin, tant qu'elle est à venir. Après un changement de mode, il
            # garde l'heure de fin du boost interrompu (constaté le 03/11/2026).
            mode = _dhw_mode(h.get("dhwZoneMode"))
            raw_end = h.get("boostModeEndTime")
            boost_end = _ts(raw_end)
            if boost_end and (boost_end <= (now or datetime.now(UTC)) or raw_end in (stopped_boosts or ())):
                boost_end = None
            if boost_end and mode in ("schedule", "boost"):
                mode = "boost"
            elif mode == "boost":
                mode = "schedule"
            else:
                boost_end = None
            app.dhw.append(
                Dhw(
                    id=str(h.get("hotWaterZoneId", "")),
                    name=_ZONE_NAMES.get(h.get("name") or "", h.get("name")) or "Eau chaude",
                    temp=_num(h.get("dhwTemperature")),
                    target=_num(h.get("targetSetpoint")),
                    comfort_setpoint=_num(h.get("comfortSetPoint")),
                    reduced_setpoint=_num(h.get("reducedSetpoint")),
                    comfort_min=_num(ranges.get("comfortSetpointMin")) or _num(h.get("setPointMin")),
                    comfort_max=_num(ranges.get("comfortSetpointMax")) or _num(h.get("setPointMax")),
                    reduced_min=_num(ranges.get("reducedSetpointMin")) or _num(h.get("setPointMin")),
                    reduced_max=_num(ranges.get("reducedSetpointMax")) or _num(h.get("setPointMax")),
                    mode=mode,
                    status="heating" if h.get("dhwStatus") == "ProducingHeat" else "idle",
                    next_switch=_ts_local(h.get("nextSwitchTime"), tz),
                    dhw_type=h.get("dhwType"),
                    program=h.get("activeDwhTimeProgramNumber"),
                    week=list(programs.get(str(h.get("hotWaterZoneId", ""))) or []),
                    boost_capable=bool(h.get("capabilityBoostMode")),
                    boost_end=boost_end,
                )
            )
        appliances.append(app)
    return appliances


# Clés masquées dans /api/v1/debug/raw : identifiants, adresses, personnes.
_SENSITIVE = re.compile(
    r"(id$|serial|mail|house|address|street|city|zip|postal|lat|lon|mac|token|password|phone|owner|user|account|gatewayname)",
    re.IGNORECASE,
)
_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[a-z]{2,}", re.IGNORECASE)
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)


def _reason(r: httpx.Response) -> str:
    """Motif court d'un refus du cloud (« Zone must be of type Tank… »), anonymisé."""
    try:
        data = r.json()
    except ValueError:
        return ""
    msg = _first(data, "message", "detail", "title", "error") if isinstance(data, dict) else None
    if not isinstance(msg, str) or not msg.strip():
        return ""
    return " : " + _UUID.sub("…", _EMAIL.sub("…", msg.strip()))[:160]


def _pseudo(value: Any) -> str:
    return f"<masqué:{hashlib.sha256(str(value).encode()).hexdigest()[:8]}>"


def anonymize(value: Any, key: str = "") -> Any:
    """Copie anonymisée : les valeurs sensibles deviennent un pseudonyme stable,
    pour garder les liens entre objets sans exposer l'identifiant réel."""
    if isinstance(value, dict):
        # Une clé peut elle-même être un identifiant (technicaldetails est indexé par appareil).
        return {(_pseudo(k) if _UUID.fullmatch(str(k)) else k): anonymize(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [anonymize(v, key) for v in value]
    if key and _SENSITIVE.search(key) and value not in (None, "", True, False):
        return _pseudo(value)
    if isinstance(value, str) and _EMAIL.search(value):
        return _EMAIL.sub("<e-mail masqué>", value)
    return value


class BdrClient:
    name = "bdr"

    def __init__(
        self,
        username: str,
        password: str,
        tz: ZoneInfo,
        client_factory: Callable[..., httpx.AsyncClient] = httpx.AsyncClient,
        user_agent: str = USER_AGENT,
    ):
        self._username = username
        self._password = password
        self._tz = tz
        # Fabrique de clients HTTP : Home Assistant passe la sienne (contexte SSL déjà chargé).
        self._client_factory = client_factory
        self._user_agent = user_agent
        self._client: httpx.AsyncClient | None = None
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._expires_at = 0.0
        self._token_lock = asyncio.Lock()
        self._auth_refused: AuthError | None = None
        self._technical: dict[str, dict[str, Any]] = {}
        self._technical_at = 0.0
        self._programs: dict[str, Any] = {}
        self._programs_at = 0.0
        self._last_dashboard: dict[str, Any] | None = None
        # Lectures complémentaires par zone ou appareil, réponses brutes des activités.
        self._extras: dict[str, dict[str, Any]] = {}
        self._activities_raw: dict[str, list[dict[str, Any]]] = {}
        self._stopped_boosts: set[str] = set()
        self._extras_at = 0.0
        self._errors_at = 0.0
        self._unsupported: dict[str, float] = {}

    async def start(self) -> None:
        self._client = self._new_client()

    def _new_client(self, follow_redirects: bool = False) -> httpx.AsyncClient:
        # L'en-tête est posé après coup : la fabrique de HA fixe déjà les siens.
        client = self._client_factory(timeout=httpx.Timeout(30.0), follow_redirects=follow_redirects)
        client.headers["User-Agent"] = self._user_agent
        return client

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()

    # --- Authentification -------------------------------------------------

    async def _login(self) -> None:
        """Parcours B2C complet ; client HTTP dédié pour repartir de cookies vides."""
        verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).decode().rstrip("=")
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        state = secrets.token_urlsafe(16)
        async with self._new_client(follow_redirects=True) as s:
            try:
                r = await s.get(
                    f"{LOGIN}/oauth2/v2.0/authorize",
                    params={
                        "response_type": "code",
                        "client_id": CLIENT_ID,
                        "redirect_uri": REDIRECT_URI,
                        "scope": SCOPE,
                        "state": state,
                        "code_challenge": challenge,
                        "code_challenge_method": "S256",
                        "p": POLICY_AUTH,
                        "brand": "remeha",
                        "lang": "en",
                        "nonce": "defaultNonce",
                        "prompt": "login",
                        "signUp": "False",
                    },
                )
                request_id = r.headers.get("x-request-id")
                csrf = s.cookies.get("x-ms-cpim-csrf")
                if not request_id or not csrf:
                    raise SourceError("connexion BDR : page d'authentification inattendue")
                tx = "StateProperties=" + base64.urlsafe_b64encode(f'{{"TID":"{request_id}"}}'.encode("ascii")).decode(
                    "ascii"
                ).rstrip("=")
                r = await s.post(
                    f"{LOGIN}/{POLICY_FORM}/SelfAsserted",
                    params={"tx": tx, "p": POLICY_FORM},
                    headers={"x-csrf-token": csrf},
                    data={"request_type": "RESPONSE", "signInName": self._username, "password": self._password},
                )
                try:
                    status = r.json().get("status")
                except ValueError:
                    status = None
                if status != "200":
                    raise AuthError("identifiants De Dietrich refusés par le cloud BDR")
                r = await s.get(
                    f"{LOGIN}/{POLICY_FORM}/api/CombinedSigninAndSignup/confirmed",
                    params={"rememberMe": "false", "csrf_token": csrf, "tx": tx, "p": POLICY_FORM},
                    follow_redirects=False,
                )
                location = r.headers.get("location", "")
                code = parse_qs(urlparse(location).query).get("code", [None])[0]
                if not code:
                    raise AuthError("connexion BDR : aucun code d'autorisation reçu")
                r = await s.post(
                    f"{LOGIN}/oauth2/v2.0/token",
                    params={"p": POLICY_AUTH},
                    data={
                        "grant_type": "authorization_code",
                        "code": code,
                        "redirect_uri": REDIRECT_URI,
                        "code_verifier": verifier,
                        "client_id": CLIENT_ID,
                    },
                )
            except httpx.HTTPError as exc:
                raise SourceError(f"cloud BDR injoignable ({type(exc).__name__})") from exc
        self._store_token(r)
        log.info("connexion au cloud BDR réussie")

    async def _refresh(self) -> bool:
        if not self._refresh_token or not self._client:
            return False
        try:
            r = await self._client.post(
                f"{LOGIN}/oauth2/v2.0/token",
                params={"p": POLICY_AUTH},
                data={"grant_type": "refresh_token", "refresh_token": self._refresh_token, "client_id": CLIENT_ID},
            )
        except httpx.HTTPError as exc:
            raise SourceError(f"cloud BDR injoignable ({type(exc).__name__})") from exc
        if r.status_code >= 400:
            log.warning("renouvellement du jeton refusé (HTTP %s), nouvelle connexion", r.status_code)
            self._refresh_token = None
            return False
        self._store_token(r)
        return True

    def _store_token(self, r: httpx.Response) -> None:
        if r.status_code >= 400:
            raise AuthError(f"jeton refusé par le cloud BDR (HTTP {r.status_code})")
        tok = r.json()
        self._access_token = tok["access_token"]
        self._refresh_token = tok.get("refresh_token", self._refresh_token)
        self._expires_at = time.monotonic() + float(tok.get("expires_in", 3600))

    async def _token(self, force: bool = False) -> str:
        async with self._token_lock:
            # Identifiants refusés : plus aucune tentative, le compte serait verrouillé. Un nouveau
            # client (rechargement avec de nouveaux identifiants) repart de zéro.
            if self._auth_refused:
                raise self._auth_refused
            if force or not self._access_token or time.monotonic() > self._expires_at - 60:
                if not await self._refresh():
                    try:
                        await self._login()
                    except AuthError as exc:
                        self._auth_refused = exc
                        raise
            assert self._access_token
            return self._access_token

    @property
    def token_valid(self) -> bool:
        return bool(self._access_token) and time.monotonic() < self._expires_at

    # --- Requêtes ---------------------------------------------------------

    async def _request(self, method: str, path: str, raise_4xx: bool = True, **kw: Any) -> httpx.Response:
        """Requête authentifiée ; raise_4xx=False rend la réponse d'un refus (hors 401 et 429)."""
        assert self._client, "start() non appelé"
        for attempt in (0, 1):
            token = await self._token(force=attempt == 1)
            try:
                r = await self._client.request(
                    method,
                    API + path,
                    headers={"Authorization": f"Bearer {token}", "Ocp-Apim-Subscription-Key": SUBSCRIPTION_KEY},
                    **kw,
                )
            except httpx.HTTPError as exc:
                raise SourceError(f"cloud BDR injoignable ({type(exc).__name__})") from exc
            if r.status_code == 401 and attempt == 0:
                continue
            if r.status_code == 429:
                raise SourceError("le cloud BDR limite les requêtes (HTTP 429)")
            if not raise_4xx and 400 <= r.status_code < 500 and r.status_code != 401:
                return r
            if r.status_code >= 400:
                raise SourceError(f"le cloud BDR a refusé la requête (HTTP {r.status_code}){_reason(r)}")
            return r
        raise AuthError("jeton refusé par le cloud BDR")

    async def _get(self, path: str, **params: Any) -> Any:
        r = await self._request("GET", path, params=params or None)
        return r.json()

    async def _post(self, path: str, body: dict[str, Any] | None = None) -> None:
        await self._request("POST", path, json=body if body is not None else {})

    # --- Lecture ----------------------------------------------------------

    async def fetch_state(self) -> State:
        dashboard = await self._get("/homes/dashboard", t=int(time.time()))
        self._last_dashboard = dashboard
        # Détails techniques (nom, passerelle) : stables, relus toutes les 6 h.
        if time.monotonic() - self._technical_at > 6 * 3600 or not self._technical:
            for a in dashboard.get("appliances") or []:
                aid = str(a.get("applianceId", ""))
                try:
                    self._technical[aid] = await self._get(f"/appliances/{aid}/technicaldetails")
                except SourceError as exc:
                    log.warning("détails techniques indisponibles : %s", exc)
            self._technical_at = time.monotonic()
        if time.monotonic() - self._programs_at > PROGRAMS_EVERY or not self._programs:
            await self._fetch_programs(dashboard)
        if time.monotonic() - self._extras_at > PROGRAMS_EVERY:
            await self._fetch_zone_extras(dashboard)
        in_error = any(
            a.get("errorStatus") and a.get("errorStatus") not in NORMAL_ERROR_STATUS
            for a in dashboard.get("appliances") or []
        )
        if in_error or time.monotonic() - self._errors_at > ERRORS_EVERY:
            await self._fetch_errors(dashboard)
        return State(
            ts=datetime.now(UTC),
            source=self.name,
            appliances=parse_dashboard(
                dashboard,
                self._technical,
                self._programs,
                self._tz,
                self._extras,
                stopped_boosts=self._stopped_boosts,
            ),
        )

    async def _optional(self, path: str) -> Any:
        """GET d'une adresse que toutes les installations n'ont pas : un refus (4xx) la met
        de côté 24 h ; None si indisponible."""
        if time.monotonic() < self._unsupported.get(path, 0):
            return None
        try:
            r = await self._request("GET", path, raise_4xx=False)
        except SourceError as exc:
            log.warning("lecture %s indisponible : %s", path.rsplit("/", 1)[-1], exc)
            return None
        if r.status_code >= 400:
            log.info("adresse %s refusée (HTTP %s), nouvel essai dans 24 h", path.rsplit("/", 1)[-1], r.status_code)
            self._unsupported[path] = time.monotonic() + UNSUPPORTED_RETRY
            return None
        try:
            return r.json()
        except ValueError:
            return None

    async def _fetch_zone_extras(self, dashboard: dict[str, Any]) -> None:
        """Activités et courbe de chauffe de chaque zone ; une lecture en échec garde la
        précédente."""
        self._extras_at = time.monotonic()
        for a in dashboard.get("appliances") or []:
            for z in a.get("climateZones") or []:
                zid = str(z.get("climateZoneId", ""))
                x = self._extras.setdefault(zid, {})
                data = await self._optional(f"/climate-zones/{zid}/activities")
                if isinstance(data, list):
                    self._activities_raw[zid] = [d for d in data if isinstance(d, dict)]
                    x["activities"] = parse_activities(data)
                data = await self._optional(f"/climate-zones/{zid}/heating-curve")
                if data is not None:
                    x["heating_curve"] = parse_heating_curve(data)

    async def _fetch_errors(self, dashboard: dict[str, Any]) -> None:
        self._errors_at = time.monotonic()
        for a in dashboard.get("appliances") or []:
            aid = str(a.get("applianceId", ""))
            data = await self._optional(f"/appliances/{aid}/activeerrors")
            if data is not None:
                self._extras.setdefault(aid, {})["active_errors"] = parse_active_errors(data)
                self._extras[aid]["active_errors_raw"] = anonymize(data)

    def _reread_extras(self) -> None:
        """Après une écriture : relecture des activités et de la courbe à la relève suivante."""
        self._extras_at = 0.0

    async def _fetch_programs(self, dashboard: dict[str, Any]) -> None:
        """Programmes de chauffage 1 à 3 de chaque zone, programme actif de l'eau chaude.
        En cas d'échec, les programmes déjà lus restent affichés."""
        self._programs_at = time.monotonic()
        for a in dashboard.get("appliances") or []:
            for z in a.get("climateZones") or []:
                zid = str(z.get("climateZoneId", ""))
                week = dict(self._programs.get(zid) or {})
                for n in (1, 2, 3):
                    try:
                        week[n] = parse_week(await self._get(f"/climate-zones/{zid}/time-programs/heating/{n}"))
                    except SourceError as exc:
                        log.warning("programme %s de la zone indisponible : %s", n, exc)
                self._programs[zid] = week
            for h in a.get("hotWaterZones") or []:
                hid, n = str(h.get("hotWaterZoneId", "")), h.get("activeDwhTimeProgramNumber")
                if n is None:
                    continue
                try:
                    self._programs[hid] = parse_week(await self._get(f"/hot-water-zones/{hid}/time-programs/{int(n)}"))
                except SourceError as exc:
                    log.warning("programme d'eau chaude indisponible : %s", exc)

    async def _energy(self, appliance_id: str, kind: str, start: datetime, end: datetime) -> list[dict[str, Any]]:
        """Lignes rapportées de /energyconsumption/<kind> entre deux instants UTC."""
        fmt = "%Y-%m-%d %H:%M:%S.%fZ"
        data = await self._get(
            f"/appliances/{appliance_id}/energyconsumption/{kind}",
            startDate=start.strftime(fmt),
            endDate=end.strftime(fmt),
        )
        return [r for r in (data or {}).get("data") or [] if r.get("isDataReported", True) and r.get("timeStamp")]

    async def fetch_consumption_days(self, appliance_id: str, start: date, end: date) -> list[DayConsumption]:
        # Le cloud découpe en jours UTC (timeStamp à minuit Z) : une plage à l'heure
        # locale chevaucherait deux jours, d'où des bornes à minuit UTC.
        rows = await self._energy(
            appliance_id,
            "daily",
            datetime.combine(start, dtime.min, UTC),
            datetime.combine(end, dtime.min, UTC) + timedelta(days=1, seconds=-1),
        )
        return [
            DayConsumption(
                day=datetime.fromisoformat(r["timeStamp"].replace("Z", "+00:00")).date(),
                heating_kwh=_num(r.get("heatingEnergyConsumed")),
                dhw_kwh=_num(r.get("hotWaterEnergyConsumed")),
                heating_delivered_kwh=_num(r.get("heatingEnergyDelivered")),
                dhw_delivered_kwh=_num(r.get("hotWaterEnergyDelivered")),
            )
            for r in rows
        ]

    async def fetch_consumption_hours(self, appliance_id: str, start: date, end: date) -> list[HourConsumption]:
        """Heures des jours locaux start à end ; le cloud n'en garde qu'une quinzaine de jours."""
        t0 = datetime.combine(start, dtime.min, self._tz).astimezone(UTC)
        t1 = datetime.combine(end + timedelta(days=1), dtime.min, self._tz).astimezone(UTC) - timedelta(seconds=1)
        rows = await self._energy(appliance_id, "hourly", t0, t1)
        return [
            HourConsumption(
                ts=datetime.fromisoformat(r["timeStamp"].replace("Z", "+00:00")).astimezone(UTC),
                heating_kwh=_num(r.get("heatingEnergyConsumed")),
                dhw_kwh=_num(r.get("hotWaterEnergyConsumed")),
            )
            for r in rows
        ]

    async def raw(self) -> dict[str, Any]:
        dashboard = self._last_dashboard or await self._get("/homes/dashboard", t=int(time.time()))
        return anonymize(
            {
                "dashboard": dashboard,
                "technicaldetails": self._technical,
                "activities": self._activities_raw,
                "extras": {
                    k: {kk: vv for kk, vv in v.items() if kk != "active_errors"} for k, v in self._extras.items()
                },
            }
        )

    # --- Commandes --------------------------------------------------------

    async def zone_manual(self, zone_id: str, setpoint: float) -> None:
        await self._post(f"/climate-zones/{zone_id}/modes/manual", {"roomTemperatureSetPoint": setpoint})

    async def zone_override(self, zone_id: str, setpoint: float) -> None:
        await self._post(f"/climate-zones/{zone_id}/modes/temporary-override", {"roomTemperatureSetPoint": setpoint})

    async def zone_schedule(self, zone_id: str, program: int) -> None:
        await self._post(f"/climate-zones/{zone_id}/modes/schedule", {"heatingProgramId": program})

    async def zone_program(self, zone_id: str, program: int) -> None:
        await self._post(f"/climate-zones/{zone_id}/time-programs/heating/{program}/activate")

    async def zone_antifrost(self, zone_id: str) -> None:
        await self._post(f"/climate-zones/{zone_id}/modes/anti-frost")

    async def zone_fireplace(self, zone_id: str, active: bool) -> None:
        await self._post(f"/climate-zones/{zone_id}/modes/fireplacemode", {"fireplaceModeActive": active})

    async def dhw_mode(self, dhw_id: str, mode: str) -> None:
        endpoint = DHW_MODE_ENDPOINT.get(mode)
        if not endpoint:
            raise SourceError(f"mode d'eau chaude non pris en charge : {mode}")
        await self._post(f"/hot-water-zones/{dhw_id}/modes/{endpoint}")
        # Le changement de mode arrête un boost en cours, mais le cloud en garde l'heure de fin.
        for a in (self._last_dashboard or {}).get("appliances") or []:
            for h in a.get("hotWaterZones") or []:
                if str(h.get("hotWaterZoneId", "")) == dhw_id and h.get("boostModeEndTime"):
                    self._stopped_boosts.add(h["boostModeEndTime"])

    async def dhw_comfort_setpoint(self, dhw_id: str, value: float) -> None:
        await self._post(f"/hot-water-zones/{dhw_id}/comfort-setpoint", {"comfortSetpoint": value})

    async def dhw_reduced_setpoint(self, dhw_id: str, value: float) -> None:
        await self._post(f"/hot-water-zones/{dhw_id}/reduced-setpoint", {"reducedSetpoint": value})

    async def dhw_boost(self, dhw_id: str) -> None:
        """Eau chaude à la consigne confort pendant environ 30 min ; accepté seulement depuis
        le mode programme. Un changement de mode l'arrête."""
        await self._post(f"/hot-water-zones/{dhw_id}/modes/boost")

    async def zone_activities(self, zone_id: str, setpoints: dict[int, float]) -> None:
        """Consignes des activités : le cloud attend la liste complète des activités de
        chauffage ; celles qui ne changent pas gardent leur valeur lue."""
        current = parse_activities(self._activities_raw.get(zone_id) or [])
        if not current:
            raise SourceError("activités du thermostat pas encore lues, réessayez dans un instant")
        unknown = set(setpoints) - {a["n"] for a in current}
        if unknown:
            raise SourceError(f"activité inconnue du thermostat : {', '.join(map(str, sorted(unknown)))}")
        body = [{"activityNumber": a["n"], "temperature": setpoints.get(a["n"], a["setpoint"])} for a in current]
        await self._request("PUT", f"/climate-zones/{zone_id}/activities/heating", json=body)
        # Écart au port : la copie lue est mise à jour tout de suite, sinon une seconde écriture
        # avant la relève suivante renverrait l'ancienne valeur de la première.
        sent = {b["activityNumber"]: b["temperature"] for b in body}
        for raw in self._activities_raw.get(zone_id) or []:
            if raw.get("activityNumber") in sent and str(raw.get("type", "Heating")).lower() == "heating":
                raw["temperature"] = sent[raw["activityNumber"]]
        self._reread_extras()

    async def zone_week(self, zone_id: str, program: int, week: list[dict[str, Any]]) -> None:
        """Créneaux d'un programme de chauffage, remplacés en entier. Affichés aussitôt,
        puis relus à la relève suivante pour voir ce que le cloud a gardé."""
        await self._request(
            "PUT", f"/climate-zones/{zone_id}/time-programs/heating/{program}", json=build_switch_points(week)
        )
        self._programs.setdefault(zone_id, {})[program] = [dict(sp) for sp in week]
        self._programs_at = 0.0

    async def dhw_week(self, dhw_id: str, week: list[dict[str, Any]]) -> None:
        """Créneaux de l'eau chaude : le cloud n'a que le programme 1."""
        await self._request("PUT", f"/hot-water-zones/{dhw_id}/time-programs/1", json=build_switch_points(week))
        self._programs[dhw_id] = [dict(sp) for sp in week]
        self._programs_at = 0.0

    async def zone_heating_curve(self, zone_id: str, slope: float, base: float) -> None:
        # Les deux paramètres sont exigés à chaque écriture.
        await self._post(f"/climate-zones/{zone_id}/heating-curve", {"slope": slope, "baseSetpoint": base})
        self._reread_extras()

    async def holiday(self, appliance_id: str, start: date | None, end: date | None) -> None:
        """Période de vacances, en jours entiers ; le cloud revient seul aux réglages
        d'avant à la fin."""
        if start is None or end is None:
            s, e = HOLIDAY_NONE
        else:
            s, e = f"{start.isoformat()}T00:00:00Z", f"{end.isoformat()}T00:00:00Z"
        await self._post(
            f"/appliances/{appliance_id}/holidayschedule", {"holidayModeStartTime": s, "holidayModeEndTime": e}
        )
