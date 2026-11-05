from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from custom_components.de_dietrich_smart.bdr.cloud import (
    anonymize,
    build_switch_points,
    parse_active_errors,
    parse_activities,
    parse_dashboard,
    parse_week,
)


def test_parse_dashboard(dashboard):
    (a,) = parse_dashboard(
        dashboard,
        {
            "a1b2c3": {
                "applianceName": "EMC-S 24/28 MI",
                "internetConnectedGateways": [{"name": "SMART TC", "softwareVersion": "1.2"}],
            }
        },
    )
    assert a.name == "EMC-S 24/28 MI"
    assert a.gateway == {"name": "SMART TC", "softwareVersion": "1.2"}
    assert a.pressure == 1.6 and a.pressure_ok is True
    assert a.in_error is False and a.online is True
    assert a.activity == "heating"
    # Pas de sonde extérieure : température « cloud » à défaut.
    assert a.outdoor_temp == 6.0 and a.outdoor_source == "météo"
    z = a.zones[0]
    assert (z.id, z.name, z.room_temp, z.setpoint) == ("z1", "Salon", 19.4, 21.0)
    assert z.mode == "override" and z.demand == "heating" and z.program == 2
    # Fin de dérogation : vrai instant UTC (contrairement à nextSwitchTime).
    assert z.override_end == datetime(2026, 11, 2, 21, 30, tzinfo=UTC)
    assert z.schedule_setpoint == 20.0 and z.next_setpoint == 17.0
    assert z.fireplace is False and z.fireplace_capable is True
    d = a.dhw[0]
    assert d.mode == "schedule" and d.status == "idle"
    assert d.temp == 49.5 and d.comfort_setpoint == 55.0 and d.reduced_min == 10.0
    # Date vide de l'API : None.
    assert d.next_switch is None


def test_error_status(dashboard):
    dashboard["appliances"][0]["errorStatus"] = "H.01.05"
    (a,) = parse_dashboard(dashboard)
    assert a.in_error is True and a.error_status == "H.01.05"
    assert a.name == "Maison Exemple"


def test_unknown_values_are_none(dashboard):
    z = dashboard["appliances"][0]["climateZones"][0]
    z["zoneMode"] = "Inattendu"
    z["roomTemperature"] = "n/a"
    (a,) = parse_dashboard(dashboard)
    assert a.zones[0].mode is None and a.zones[0].room_temp is None


def test_anonymize_keeps_structure(dashboard):
    dashboard["appliances"][0]["ownerEmail"] = "test@example.org"
    dashboard["appliances"][0]["note"] = "contact test@example.org"
    out = anonymize(dashboard)
    a = out["appliances"][0]
    assert a["applianceId"].startswith("<masqué:")
    assert a["houseName"].startswith("<masqué:")
    assert a["ownerEmail"].startswith("<masqué:")
    assert "example.org" not in a["note"]
    # Pseudonyme stable : les liens entre objets restent lisibles.
    assert a["climateZones"][0]["applianceId"] == a["applianceId"]
    assert a["waterPressure"] == 1.6 and a["climateZones"][0]["name"] == "Salon"


def test_real_sample():
    """Échantillon réel (EMC-S 24/28 MI + SMART TC° filaire), anonymisé."""
    import json
    from pathlib import Path

    raw = json.loads((Path(__file__).parents[1] / "fixtures" / "reel-2026-11-02.json").read_text())
    (a,) = parse_dashboard(raw["dashboard"], raw["technicaldetails"])
    assert a.name == "Appareil" and a.gateway["name"] == "RU2" and a.gateway["softwareVersion"] == "2.28.6"
    assert a.online is True and a.in_error is False and a.activity == "idle"
    assert a.pressure == 1.1 and a.pressure_ok is True
    # Pas de sonde extérieure raccordée : température météo du cloud.
    assert a.outdoor_temp == 5.0 and a.outdoor_source == "météo"
    assert a.holiday_active is False
    z = a.zones[0]
    assert (z.name, z.room_temp, z.setpoint, z.mode, z.program) == ("Séjour", 20.0, 19.5, "manual", 2)
    assert z.schedule_setpoint == 18.0 and z.next_setpoint == 19.0 and z.override_end is None
    assert z.demand == "idle" and z.fireplace_capable is True
    d = a.dhw[0]
    # Chaudière mixte instantanée : pas de température d'eau chaude mesurée.
    assert d.name == "Eau chaude" and d.temp is None and d.target == 45.0
    assert d.mode == "schedule" and d.status == "idle" and d.dhw_type == "Combi"
    assert (d.comfort_setpoint, d.reduced_setpoint, d.reduced_min) == (50.0, 15.0, 10.0)


def test_anonymize_masks_uuid_keys():
    uid = "0b9c6f3e-1a2b-4c3d-8e9f-001122334455"
    out = anonymize({"technicaldetails": {uid: {"applianceName": "Appareil"}}, "applianceId": uid})
    assert uid not in str(out)
    # Même pseudonyme pour la clé et la valeur : le lien reste lisible.
    assert list(out["technicaldetails"]) == [out["applianceId"]]


def test_dhw_off_is_eco(dashboard):
    # La commande anti-frost (« Éco ») se relit « Off » (constaté le 02/11/2026).
    dashboard["appliances"][0]["hotWaterZones"][0]["dhwZoneMode"] = "Off"
    (a,) = parse_dashboard(dashboard)
    assert a.dhw[0].mode == "eco"


def test_parse_week_and_programs(dashboard):
    raw = {
        "timeProgramNumber": 1,
        "type": "Heating",
        "switchPoints": [
            {"day": 1, "time": "06:30", "activity": 4, "customValue": None},
            {"day": 1, "time": "22:00", "activity": 1, "customValue": 17.5},
            {"day": 9, "time": "08:00", "activity": 2, "customValue": None},
            {"day": 2, "time": "8h", "activity": 2, "customValue": None},
        ],
    }
    week = parse_week(raw)
    assert week == [
        {"day": 1, "time": "06:30", "activity": 4, "setpoint": None},
        {"day": 1, "time": "22:00", "activity": 1, "setpoint": 17.5},
    ]
    dashboard["appliances"][0]["hotWaterZones"][0]["activeDwhTimeProgramNumber"] = 1
    (a,) = parse_dashboard(dashboard, programs={"z1": {1: week}, "h1": week[:1]})
    assert a.zones[0].week == {1: week}
    assert a.dhw[0].program == 1 and a.dhw[0].week == week[:1]


def test_next_switch_is_local(dashboard):
    dashboard["appliances"][0]["climateZones"][0]["nextSwitchTime"] = "2026-11-03T22:00:00Z"
    (a,) = parse_dashboard(dashboard)
    assert a.zones[0].next_switch == datetime(2026, 11, 3, 22, 0, tzinfo=ZoneInfo("Europe/Paris"))


def test_holiday_boost_and_extras(dashboard):
    a0 = dashboard["appliances"][0]
    a0["holidaySchedule"] = {"startTime": "2026-12-24T00:00:01Z", "endTime": "2027-01-02T00:00:01Z", "active": False}
    a0["seasonalMode"] = "Winter"
    h = a0["hotWaterZones"][0]
    h.update({"dhwZoneMode": "Boost", "capabilityBoostMode": True, "boostModeEndTime": "2026-11-03T13:11:59.2692621Z"})
    acts = [{"n": 1, "name": "Nuit", "setpoint": 18.0, "min": 5.0, "max": 30.0, "step": 0.5}]
    now = datetime(2026, 11, 3, 13, 0, tzinfo=UTC)
    (a,) = parse_dashboard(dashboard, extras={"z1": {"activities": acts}, "a1b2c3": {"active_errors": []}}, now=now)
    assert (a.holiday_start, a.holiday_end, a.holiday_active) == ("2026-12-24", "2027-01-02", False)
    assert a.seasonal_mode == "Winter" and a.active_errors == []
    d = a.dhw[0]
    assert d.mode == "boost" and d.boost_capable is True
    assert d.boost_end.astimezone(UTC).strftime("%H:%M") == "13:11"
    assert a.zones[0].activities == acts and a.zones[0].heating_curve is None
    # Sans période de vacances (date vide de l'API) : None.
    (b,) = parse_dashboard(
        {
            "appliances": [
                {**a0, "holidaySchedule": {"startTime": "0001-01-01T00:00:01Z", "endTime": "0001-01-01T00:00:01Z"}}
            ]
        }
    )
    assert b.holiday_start is None and b.holiday_end is None


def test_parse_active_errors_shapes():
    assert parse_active_errors([]) == []
    (e,) = parse_active_errors(
        {"activeErrors": [{"errorCode": "H.01.05", "description": "Écart départ-retour", "deviceId": "x"}]}
    )
    assert e["code"] == "H.01.05" and e["text"] == "Écart départ-retour" and e["raw"]["deviceId"].startswith("<masqué:")
    assert parse_active_errors(["A.02.06"])[0]["code"] == "A.02.06"
    assert parse_active_errors({"inattendu": 1}) == []


def test_activity_default_names_in_french():
    acts = parse_activities(
        [
            {"activityNumber": 2, "type": "Heating", "name": "Home", "temperature": 19},
            {"activityNumber": 1, "type": "Heating", "name": " sleep ", "temperature": 18},
            {"activityNumber": 3, "type": "Heating", "name": "Télétravail", "temperature": 19.5},
            {"activityNumber": 4, "type": "Heating", "name": "", "temperature": 19},
            {"activityNumber": 5, "type": "Cooling", "name": "Evening", "temperature": 25},
        ]
    )
    assert [(a["n"], a["name"]) for a in acts] == [(1, "Nuit"), (2, "Maison"), (3, "Télétravail"), (4, None)]


def test_boost_read_from_end_time(dashboard):
    """Constaté le 03/11/2026 : pendant le boost, dhwZoneMode reste « Scheduling » ; après
    un passage en « Off », boostModeEndTime reste annoncée."""
    h = dashboard["appliances"][0]["hotWaterZones"][0]
    end = "2026-11-03T15:49:54.2866573Z"
    during, after = datetime(2026, 11, 3, 15, 21, tzinfo=UTC), datetime(2026, 11, 3, 15, 50, tzinfo=UTC)
    h.update({"dhwZoneMode": "Scheduling", "capabilityBoostMode": True, "boostModeEndTime": end})
    d = parse_dashboard(dashboard, now=during)[0].dhw[0]
    assert d.mode == "boost" and d.boost_end.strftime("%H:%M") == "15:49"
    d = parse_dashboard(dashboard, now=after)[0].dhw[0]
    assert d.mode == "schedule" and d.boost_end is None
    d = parse_dashboard(dashboard, now=during, stopped_boosts={end})[0].dhw[0]
    assert d.mode == "schedule" and d.boost_end is None
    h["dhwZoneMode"] = "Off"
    d = parse_dashboard(dashboard, now=during)[0].dhw[0]
    assert d.mode == "eco" and d.boost_end is None


def test_week_dhw_and_round_trip():
    # Eau chaude : créneaux confort/réduit (constaté sur le programme 1 réel).
    dhw = {
        "switchPoints": [
            {"day": 1, "time": "06:00", "comfortActive": True},
            {"day": 1, "time": "22:00:00", "comfortActive": False},
        ]
    }
    assert parse_week(dhw) == [
        {"day": 1, "time": "06:00", "comfort": True},
        {"day": 1, "time": "22:00", "comfort": False},
    ]
    assert build_switch_points(parse_week(dhw))["switchPoints"][1] == {
        "day": 1,
        "time": "22:00",
        "comfortActive": False,
    }
    heating = {
        "switchPoints": [
            {"day": 0, "time": "08:00", "activity": 2, "customValue": None},
            {"day": 1, "time": "06:30", "activity": 4, "customValue": 19.5},
        ]
    }
    assert build_switch_points(parse_week(heating)) == heating
    # Semaine envoyée triée, quel que soit l'ordre de saisie.
    out = build_switch_points(list(reversed(parse_week(heating))))
    assert [(x["day"], x["time"]) for x in out["switchPoints"]] == [(0, "08:00"), (1, "06:30")]


def test_local_z_times_across_dst():
    """Heures « locales suivies de Z » : lues à l'heure de Paris, y compris le jour du passage à l'heure d'été."""
    from custom_components.de_dietrich_smart.bdr.cloud import PARIS, _ts, _ts_local

    summer = _ts_local("2027-03-28T03:00:00Z", PARIS)
    assert summer is not None and summer.isoformat() == "2027-03-28T03:00:00+02:00"
    winter = _ts_local("2027-03-28T01:30:00Z", PARIS)
    assert winter is not None and winter.isoformat() == "2027-03-28T01:30:00+01:00"
    # Heures de consommation : vrais instants UTC.
    hour = _ts("2027-03-28T01:00:00Z")
    assert hour is not None and hour.utcoffset() == timedelta(0) and hour.hour == 1
