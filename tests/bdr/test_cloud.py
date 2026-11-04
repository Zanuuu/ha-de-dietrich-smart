"""Client BDR contre un cloud simulé (respx) : connexion B2C, renouvellement, erreurs."""

import json
from datetime import date
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx

from custom_components.de_dietrich_smart.bdr import cloud as b
from custom_components.de_dietrich_smart.bdr.models import AuthError, SourceError

TZ = ZoneInfo("Europe/Paris")


def mock_login(router, ok=True, token="tok-1"):
    router.get(f"{b.LOGIN}/oauth2/v2.0/authorize").mock(
        return_value=httpx.Response(
            200, headers={"x-request-id": "req-42", "set-cookie": "x-ms-cpim-csrf=csrf-1; Path=/"}, text="<html/>"
        )
    )
    router.post(f"{b.LOGIN}/{b.POLICY_FORM}/SelfAsserted").mock(
        return_value=httpx.Response(200, json={"status": "200" if ok else "400"})
    )
    router.get(f"{b.LOGIN}/{b.POLICY_FORM}/api/CombinedSigninAndSignup/confirmed").mock(
        return_value=httpx.Response(302, headers={"location": f"{b.REDIRECT_URI}?state=x&code=code-123"})
    )
    return router.post(f"{b.LOGIN}/oauth2/v2.0/token").mock(
        return_value=httpx.Response(200, json={"access_token": token, "refresh_token": "ref-1", "expires_in": 3600})
    )


@pytest.fixture
def src():
    return b.BdrClient("julie@example.org", "mot-de-passe", TZ)


@respx.mock
async def test_login_and_dashboard(src, dashboard):
    token = mock_login(respx)
    dash = respx.get(f"{b.API}/homes/dashboard").mock(return_value=httpx.Response(200, json=dashboard))
    respx.get(f"{b.API}/appliances/a1b2c3/technicaldetails").mock(
        return_value=httpx.Response(200, json={"applianceName": "EMC-S"})
    )
    respx.get(url__regex=r".*/time-programs/.*").mock(return_value=httpx.Response(200, json={"switchPoints": []}))
    respx.get(url__regex=r".*/(activities|heating-curve|activeerrors)$").mock(return_value=httpx.Response(404))
    await src.start()
    state = await src.fetch_state()
    await src.close()
    assert state.zone.room_temp == 19.4 and state.appliance.name == "EMC-S"
    # Formulaire B2C : identifiants et jeton CSRF attendus.
    form = respx.calls[1].request
    assert form.headers["x-csrf-token"] == "csrf-1"
    assert b"signInName=julie%40example.org" in form.content
    assert form.url.params["tx"].startswith("StateProperties=")
    # Échange du code avec le vérificateur PKCE.
    body = token.calls[0].request.content.decode()
    assert "grant_type=authorization_code" in body and "code=code-123" in body and "code_verifier=" in body
    req = dash.calls[0].request
    assert req.headers["Authorization"] == "Bearer tok-1"
    assert req.headers["Ocp-Apim-Subscription-Key"] == b.SUBSCRIPTION_KEY


@respx.mock
async def test_bad_credentials(src):
    mock_login(respx, ok=False)
    await src.start()
    with pytest.raises(AuthError):
        await src.fetch_state()
    await src.close()


@respx.mock
async def test_401_triggers_refresh_then_retry(src, dashboard):
    token = mock_login(respx)
    calls = {"n": 0}

    def dash(request):
        calls["n"] += 1
        return httpx.Response(401) if calls["n"] == 1 else httpx.Response(200, json=dashboard)

    respx.get(f"{b.API}/homes/dashboard").mock(side_effect=dash)
    respx.get(url__regex=r".*/technicaldetails").mock(return_value=httpx.Response(200, json={}))
    respx.get(url__regex=r".*/time-programs/.*").mock(return_value=httpx.Response(200, json={"switchPoints": []}))
    respx.get(url__regex=r".*/(activities|heating-curve|activeerrors)$").mock(return_value=httpx.Response(404))
    await src.start()
    state = await src.fetch_state()
    await src.close()
    assert state.zone is not None
    grants = [c.request.content.decode() for c in token.calls]
    assert any("grant_type=refresh_token" in g for g in grants)


@respx.mock
async def test_http_errors_are_readable(src):
    mock_login(respx)
    respx.get(f"{b.API}/homes/dashboard").mock(return_value=httpx.Response(429))
    await src.start()
    with pytest.raises(SourceError, match="limite"):
        await src.fetch_state()
    await src.close()


@respx.mock
async def test_commands_payloads(src):
    mock_login(respx)
    routes = {
        "manual": respx.post(f"{b.API}/climate-zones/z1/modes/manual").mock(return_value=httpx.Response(200)),
        "override": respx.post(f"{b.API}/climate-zones/z1/modes/temporary-override").mock(
            return_value=httpx.Response(200)
        ),
        "schedule": respx.post(f"{b.API}/climate-zones/z1/modes/schedule").mock(return_value=httpx.Response(200)),
        "program": respx.post(f"{b.API}/climate-zones/z1/time-programs/heating/3/activate").mock(
            return_value=httpx.Response(200)
        ),
        "dhw": respx.post(f"{b.API}/hot-water-zones/h1/modes/continuous-comfort").mock(
            return_value=httpx.Response(200)
        ),
        "comfort": respx.post(f"{b.API}/hot-water-zones/h1/comfort-setpoint").mock(return_value=httpx.Response(200)),
    }
    await src.start()
    await src.zone_manual("z1", 20.5)
    await src.zone_override("z1", 21.0)
    await src.zone_schedule("z1", 2)
    await src.zone_program("z1", 3)
    await src.dhw_mode("h1", "comfort")
    await src.dhw_comfort_setpoint("h1", 52.0)
    await src.close()
    assert json.loads(routes["manual"].calls[0].request.content) == {"roomTemperatureSetPoint": 20.5}
    assert json.loads(routes["schedule"].calls[0].request.content) == {"heatingProgramId": 2}
    assert json.loads(routes["comfort"].calls[0].request.content) == {"comfortSetpoint": 52.0}
    assert all(r.called for r in routes.values())


def _energy_row(ts, heating, dhw, reported=True):
    return {
        "isDataReported": reported,
        "timeStamp": ts,
        "heatingEnergyConsumed": heating,
        "hotWaterEnergyConsumed": dhw,
        "heatingEnergyDelivered": 0,
    }


@respx.mock
async def test_daily_consumption_utc_days(src):
    mock_login(respx)
    # Réponse réelle du 03/11/2026 : des jours UTC, horodatés à minuit Z.
    route = respx.get(f"{b.API}/appliances/a1/energyconsumption/daily").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    _energy_row("2026-11-01T00:00:00+00:00", 12.0, 2.0),
                    _energy_row("2026-11-02T00:00:00+00:00", 9.0, 2.0),
                ]
            },
        )
    )
    await src.start()
    days = await src.fetch_consumption_days("a1", date(2026, 11, 1), date(2026, 11, 2))
    await src.close()
    # Un jour par ligne : plus d'addition de deux jours UTC dans un jour local.
    assert [(c.day, c.heating_kwh, c.dhw_kwh) for c in days] == [
        (date(2026, 11, 1), 12.0, 2.0),
        (date(2026, 11, 2), 9.0, 2.0),
    ]
    q = route.calls[0].request.url.params
    assert q["startDate"].startswith("2026-11-01 00:00:00")
    assert q["endDate"].startswith("2026-11-02 23:59:59")


@respx.mock
async def test_hourly_consumption(src):
    mock_login(respx)
    route = respx.get(f"{b.API}/appliances/a1/energyconsumption/hourly").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    _energy_row("2026-11-02T23:00:00+00:00", 2.0, 0),
                    _energy_row("2026-11-03T00:00:00+00:00", 0, 1.0),
                    _energy_row("2026-11-03T21:00:00+00:00", 0, 0, reported=False),
                ]
            },
        )
    )
    await src.start()
    hours = await src.fetch_consumption_hours("a1", date(2026, 11, 3), date(2026, 11, 3))
    await src.close()
    # Les heures à venir (isDataReported false) ne sont pas des zéros.
    assert [(h.ts.isoformat(), h.heating_kwh, h.dhw_kwh) for h in hours] == [
        ("2026-11-02T23:00:00+00:00", 2.0, 0.0),
        ("2026-11-03T00:00:00+00:00", 0.0, 1.0),
    ]
    q = route.calls[0].request.url.params
    # Jour local de Paris (UTC+1 en novembre) exprimé en UTC.
    assert q["startDate"].startswith("2026-11-02 23:00:00")
    assert q["endDate"].startswith("2026-11-03 22:59:59")


@respx.mock
async def test_programs_read_with_state(src, dashboard):
    mock_login(respx)
    dashboard["appliances"][0]["hotWaterZones"][0]["activeDwhTimeProgramNumber"] = 1
    respx.get(f"{b.API}/homes/dashboard").mock(return_value=httpx.Response(200, json=dashboard))
    respx.get(url__regex=r".*/technicaldetails").mock(return_value=httpx.Response(200, json={}))
    week = {"switchPoints": [{"day": 1, "time": "06:00", "activity": 2, "customValue": None}]}
    heating = respx.get(url__regex=r".*/climate-zones/z1/time-programs/heating/[12]$").mock(
        return_value=httpx.Response(200, json=week)
    )
    respx.get(f"{b.API}/climate-zones/z1/time-programs/heating/3").mock(return_value=httpx.Response(500))
    dhw = respx.get(f"{b.API}/hot-water-zones/h1/time-programs/1").mock(return_value=httpx.Response(200, json=week))
    respx.get(url__regex=r".*/(activities|heating-curve|activeerrors)$").mock(return_value=httpx.Response(404))
    await src.start()
    state = await src.fetch_state()
    # Relève suivante : programmes en cache, pas de nouvelle lecture.
    await src.fetch_state()
    await src.close()
    sp = {"day": 1, "time": "06:00", "activity": 2, "setpoint": None}
    # Programme 3 en échec : les autres restent lus.
    assert state.zone.week == {1: [sp], 2: [sp]}
    assert state.dhw.week == [sp] and state.dhw.program == 1
    assert heating.call_count == 2 and dhw.call_count == 1


ACTIVITIES = [
    {"type": "Cooling", "activityNumber": 1, "name": "Sleep", "temperature": 26.0},
    {
        "type": "Heating",
        "activityNumber": 2,
        "name": "Maison",
        "temperature": 19.0,
        "setPointMin": 5,
        "setPointMax": 30,
        "increment": 0.5,
    },
    {
        "type": "Heating",
        "activityNumber": 1,
        "name": "Nuit",
        "temperature": 18.0,
        "setPointMin": 5,
        "setPointMax": 30,
        "increment": 0.5,
    },
]
CURVE = {
    "slope": 1.2,
    "minimumSlope": 0.2,
    "maximumSlope": 4.0,
    "incrementSlope": 0.1,
    "baseSetpoint": 20.0,
    "minimumBaseSetpoint": 15,
    "maximumBaseSetpoint": 90,
    "incrementBaseSetpoint": 0.5,
    "maxFlowTemperature": 75,
}


@respx.mock
async def test_extras_read_and_unsupported_cached(src, dashboard):
    mock_login(respx)
    respx.get(f"{b.API}/homes/dashboard").mock(return_value=httpx.Response(200, json=dashboard))
    respx.get(url__regex=r".*/technicaldetails").mock(return_value=httpx.Response(200, json={}))
    respx.get(url__regex=r".*/time-programs/.*").mock(return_value=httpx.Response(200, json={"switchPoints": []}))
    respx.get(f"{b.API}/climate-zones/z1/activities").mock(return_value=httpx.Response(200, json=ACTIVITIES))
    respx.get(f"{b.API}/climate-zones/z1/heating-curve").mock(return_value=httpx.Response(200, json=CURVE))
    errors = respx.get(f"{b.API}/appliances/a1b2c3/activeerrors").mock(return_value=httpx.Response(404))
    await src.start()
    state = await src.fetch_state()
    src._errors_at = 0  # échéance des 10 min dépassée
    await src.fetch_state()
    await src.close()
    z = state.zone
    assert [(a["n"], a["name"], a["setpoint"]) for a in z.activities] == [(1, "Nuit", 18.0), (2, "Maison", 19.0)]
    assert z.heating_curve["slope"] == 1.2 and z.heating_curve["base_max"] == 90 and z.heating_curve["max_flow"] == 75
    assert z.preheat_enabled is False
    # Adresse refusée : pas de nouvel essai avant 24 h ; relève non bloquée.
    assert errors.call_count == 1 and state.appliance.active_errors is None


@respx.mock
async def test_new_commands_payloads(src, dashboard):
    mock_login(respx)
    respx.get(f"{b.API}/homes/dashboard").mock(return_value=httpx.Response(200, json=dashboard))
    respx.get(url__regex=r".*/technicaldetails").mock(return_value=httpx.Response(200, json={}))
    respx.get(url__regex=r".*/time-programs/.*").mock(return_value=httpx.Response(200, json={"switchPoints": []}))
    respx.get(f"{b.API}/climate-zones/z1/activities").mock(return_value=httpx.Response(200, json=ACTIVITIES))
    respx.get(url__regex=r".*/(heating-curve|activeerrors)$").mock(return_value=httpx.Response(404))
    routes = {
        "acts": respx.put(f"{b.API}/climate-zones/z1/activities/heating").mock(return_value=httpx.Response(200)),
        "curve": respx.post(f"{b.API}/climate-zones/z1/heating-curve").mock(return_value=httpx.Response(200)),
        "boost": respx.post(f"{b.API}/hot-water-zones/h1/modes/boost").mock(return_value=httpx.Response(200)),
        "holiday": respx.post(f"{b.API}/appliances/a1/holidayschedule").mock(return_value=httpx.Response(200)),
    }
    await src.start()
    with pytest.raises(SourceError, match="pas encore lues"):
        await src.zone_activities("z1", {1: 17.0})
    await src.fetch_state()
    await src.zone_activities("z1", {1: 17.5})
    with pytest.raises(SourceError, match="inconnue"):
        await src.zone_activities("z1", {7: 17.0})
    await src.zone_heating_curve("z1", 1.4, 21.0)
    await src.dhw_boost("h1")
    await src.holiday("a1", date(2026, 12, 24), date(2027, 1, 2))
    await src.holiday("a1", None, None)
    await src.close()
    body = lambda r, i=0: json.loads(r.calls[i].request.content)  # noqa: E731
    # Liste complète des activités de chauffage, seule la modifiée change.
    assert body(routes["acts"]) == [
        {"activityNumber": 1, "temperature": 17.5},
        {"activityNumber": 2, "temperature": 19.0},
    ]
    assert body(routes["curve"]) == {"slope": 1.4, "baseSetpoint": 21.0}
    assert routes["boost"].called
    assert body(routes["holiday"]) == {
        "holidayModeStartTime": "2026-12-24T00:00:00Z",
        "holidayModeEndTime": "2027-01-02T00:00:00Z",
    }
    assert body(routes["holiday"], 1) == {
        "holidayModeStartTime": "0001-01-01T00:00:00Z",
        "holidayModeEndTime": "0001-01-01T00:00:01Z",
    }


@respx.mock
async def test_refusal_reason_is_shown(src):
    mock_login(respx)
    respx.post(f"{b.API}/hot-water-zones/h1/reduced-setpoint").mock(
        return_value=httpx.Response(400, json={"message": "Zone must be of type Tank to do this process."})
    )
    await src.start()
    with pytest.raises(SourceError, match=r"HTTP 400.*Tank"):
        await src.dhw_reduced_setpoint("h1", 40.0)
    await src.close()


@respx.mock
async def test_week_written_whole_and_shown_at_once(src, dashboard):
    mock_login(respx)
    respx.get(f"{b.API}/homes/dashboard").mock(return_value=httpx.Response(200, json=dashboard))
    respx.get(url__regex=r".*/technicaldetails").mock(return_value=httpx.Response(200, json={}))
    reads = respx.get(url__regex=r".*/time-programs/.*").mock(
        return_value=httpx.Response(200, json={"switchPoints": []})
    )
    respx.get(url__regex=r".*/(activities|heating-curve|activeerrors)$").mock(return_value=httpx.Response(404))
    heating = respx.put(f"{b.API}/climate-zones/z1/time-programs/heating/3").mock(return_value=httpx.Response(200))
    dhw = respx.put(f"{b.API}/hot-water-zones/h1/time-programs/1").mock(return_value=httpx.Response(200))
    await src.start()
    await src.fetch_state()
    before = reads.call_count
    week = [
        {"day": 1, "time": "17:30", "activity": 5, "setpoint": None},
        {"day": 1, "time": "06:30", "activity": 4, "setpoint": 19.5},
    ]
    await src.zone_week("z1", 3, week)
    await src.dhw_week("h1", [{"day": 0, "time": "07:00", "comfort": True}])
    state = await src.fetch_state()
    await src.close()
    # Corps trié par jour et heure ; consigne propre en customValue.
    assert json.loads(heating.calls[0].request.content) == {
        "switchPoints": [
            {"day": 1, "time": "06:30", "activity": 4, "customValue": 19.5},
            {"day": 1, "time": "17:30", "activity": 5, "customValue": None},
        ]
    }
    assert json.loads(dhw.calls[0].request.content) == {
        "switchPoints": [{"day": 0, "time": "07:00", "comfortActive": True}]
    }
    # Programmes relus à la relève qui suit l'écriture.
    assert reads.call_count > before
    assert state.zone.week[3] == []


@respx.mock
async def test_client_factory_used(dashboard):
    """Home Assistant fournit ses clients HTTP : le client n'en crée aucun lui-même."""
    mock_login(respx)
    respx.get(f"{b.API}/homes/dashboard").mock(return_value=httpx.Response(200, json=dashboard))
    respx.get(url__regex=rf"{b.API}/.*").mock(return_value=httpx.Response(404))
    made: list[dict] = []

    def factory(**kw):
        made.append(kw)
        return httpx.AsyncClient(**kw)

    src = b.BdrClient("julie@example.org", "mot-de-passe", TZ, client_factory=factory)
    await src.start()
    await src.fetch_state()
    await src.close()
    assert [k["follow_redirects"] for k in made] == [False, True]
    assert all(c.request.headers["User-Agent"].startswith("ha-de-dietrich-smart") for c in respx.calls)


@respx.mock
async def test_sequential_activity_writes_keep_first(src, dashboard):
    """Deux consignes d'activité de suite : la seconde écriture garde la première."""
    mock_login(respx)
    respx.get(f"{b.API}/homes/dashboard").mock(return_value=httpx.Response(200, json=dashboard))
    respx.get(url__regex=r".*/technicaldetails").mock(return_value=httpx.Response(200, json={}))
    respx.get(url__regex=r".*/time-programs/.*").mock(return_value=httpx.Response(200, json={"switchPoints": []}))
    respx.get(f"{b.API}/climate-zones/z1/activities").mock(return_value=httpx.Response(200, json=ACTIVITIES))
    respx.get(url__regex=r".*/(heating-curve|activeerrors)$").mock(return_value=httpx.Response(404))
    put = respx.put(f"{b.API}/climate-zones/z1/activities/heating").mock(return_value=httpx.Response(200))
    await src.start()
    await src.fetch_state()
    await src.zone_activities("z1", {1: 17.5})
    await src.zone_activities("z1", {2: 20.0})
    await src.close()
    # La seconde écriture (avant toute relève) garde la consigne Nuit envoyée par la première.
    assert json.loads(put.calls[1].request.content) == [
        {"activityNumber": 1, "temperature": 17.5},
        {"activityNumber": 2, "temperature": 20.0},
    ]


@respx.mock
async def test_login_refused_is_not_retried(src):
    """Identifiants refusés : plus aucune tentative de connexion (verrouillage du compte)."""
    mock_login(respx, ok=False)
    await src.start()
    with pytest.raises(AuthError):
        await src.fetch_state()
    form = respx.routes[1]
    calls = form.call_count
    with pytest.raises(AuthError):
        await src.fetch_state()
    with pytest.raises(AuthError):
        await src.fetch_consumption_days("a1", date(2026, 11, 1), date(2026, 11, 2))
    await src.close()
    assert form.call_count == calls == 1


@respx.mock
async def test_user_agent_given_by_caller(dashboard):
    """La version annoncée au cloud est celle que fournit l'intégration (son manifest)."""
    mock_login(respx)
    respx.get(f"{b.API}/homes/dashboard").mock(return_value=httpx.Response(200, json=dashboard))
    respx.get(url__regex=rf"{b.API}/.*").mock(return_value=httpx.Response(404))
    src = b.BdrClient("julie@example.org", "mot-de-passe", TZ, user_agent="ha-de-dietrich-smart/9.9.9")
    await src.start()
    await src.fetch_state()
    await src.close()
    assert {c.request.headers["User-Agent"] for c in respx.calls} == {"ha-de-dietrich-smart/9.9.9"}
