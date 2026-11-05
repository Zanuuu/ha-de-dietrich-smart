"""Événements tirés de la comparaison de deux relèves (port de poller._diff)."""

import copy

import pytest

from custom_components.de_dietrich_smart.events import APP_ORIGIN, EXT_ORIGIN, diff, initial


def ext(_key, _value):
    return EXT_ORIGIN


def changed(state, mutate):
    new = copy.deepcopy(state)
    mutate(new)
    return new


def messages(events):
    return [e["message"] for e in events]


def test_no_change_no_event(state):
    assert diff(state, copy.deepcopy(state), ext) == []


def test_fault_appears_and_clears(state):
    def fault(s):
        s.appliance.in_error, s.appliance.error_status = True, "Locking"

    faulty = changed(state, fault)
    (e,) = diff(state, faulty, ext)
    assert (e["kind"], e["level"], e["message"]) == ("defaut", "critical", "Défaut apparu : Locking")
    (e,) = diff(faulty, state, ext)
    assert (e["level"], e["message"]) == ("good", "Défaut disparu (était Locking)")


def test_initial_fault_event(state):
    assert initial(state) == []
    s = changed(state, lambda s: setattr(s.appliance, "in_error", True))
    assert messages(initial(s)) == [f"Défaut actif : {s.appliance.error_status}"]


@pytest.mark.parametrize(
    ("pressure", "ok", "expected"), [(0.9, True, "Pression basse : 0,9 bar"), (1.2, False, "Pression basse : 1,2 bar")]
)
def test_low_pressure(state, pressure, ok, expected):
    def low(s):
        s.appliance.pressure, s.appliance.pressure_ok = pressure, ok

    low_state = changed(state, low)
    assert messages(diff(state, low_state, ext)) == [expected]
    assert messages(diff(low_state, state, ext)) == ["Pression revenue à 1,6 bar"]


def test_room_temp_lost_and_back(state):
    lost = changed(state, lambda s: setattr(s.zone, "room_temp", None))
    assert messages(diff(state, lost, ext)) == ["Température ambiante plus transmise par le cloud"]
    assert diff(lost, copy.deepcopy(lost), ext) == []
    assert messages(diff(lost, state, ext)) == ["Température ambiante de nouveau transmise : 19,4 °C"]


def test_mode_with_setpoint_single_event(state):
    def manual(s):
        s.zone.mode, s.zone.setpoint = "manual", 20.0

    (e,) = diff(state, changed(state, manual), ext)
    assert e["message"] == "Mode : Dérogation → Manuel, consigne 21 °C → 20 °C"
    assert e["data"]["origin"] == EXT_ORIGIN


def test_curve_change_uses_origin(state):
    def curve(s):
        s.zone.heating_curve = {"slope": 0.8, "base": 24.0}

    before = changed(state, lambda s: setattr(s.zone, "heating_curve", {"slope": 1.5, "base": 15.0}))
    (e,) = diff(before, changed(state, curve), lambda k, v: APP_ORIGIN if k == "z1:curve" else EXT_ORIGIN)
    assert e["message"] == "Courbe de chauffe : pente 1,5 → 0,8, pied 15 °C → 24 °C"
    assert e["data"]["origin"] == APP_ORIGIN


def test_program_and_fireplace(state):
    def both(s):
        s.zone.program, s.zone.fireplace = 3, True

    assert messages(diff(state, changed(state, both), ext)) == ["Programme actif : 2 → 3", "Mode cheminée activé"]


def test_dhw_mode_and_setpoint(state):
    def dhw(s):
        s.dhw.mode, s.dhw.comfort_setpoint = "eco", 50.0

    assert messages(diff(state, changed(state, dhw), ext)) == [
        "Eau chaude : Programme → Éco",
        "Consigne eau chaude confort : 55 °C → 50 °C",
    ]


def test_events_carry_timestamp_and_kind(state):
    (e,) = diff(state, changed(state, lambda s: setattr(s.zone, "program", 1)), ext)
    assert e["kind"] == "programme" and e["level"] == "info" and e["ts"] > 0
