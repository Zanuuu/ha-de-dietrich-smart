"""Programmes horaires : changement en vigueur, relevé des consignes d'activité."""

from datetime import datetime
from zoneinfo import ZoneInfo

from custom_components.de_dietrich_smart.bdr import programs as p

TZ = ZoneInfo("Europe/Paris")
# Programme 1 réel (extrait) : semaine 06:30 Matin, 09:00 Absent, 17:30 Soir, 22:00 Nuit ;
# dimanche 08:00 Maison, 22:00 Nuit.
WEEK = [
    {"day": 0, "time": "08:00", "activity": 2, "setpoint": None},
    {"day": 0, "time": "22:00", "activity": 1, "setpoint": None},
    {"day": 2, "time": "06:30", "activity": 4, "setpoint": None},
    {"day": 2, "time": "09:00", "activity": 3, "setpoint": None},
    {"day": 2, "time": "17:30", "activity": 5, "setpoint": None},
    {"day": 2, "time": "22:00", "activity": 1, "setpoint": 17.0},
]


def test_cloud_day():
    assert p.cloud_day(datetime(2026, 11, 1, tzinfo=TZ)) == 0  # dimanche
    assert p.cloud_day(datetime(2026, 11, 3, tzinfo=TZ)) == 2  # mardi


def test_current_switch():
    sp, since = p.current_switch(WEEK, datetime(2026, 11, 3, 10, 15, tzinfo=TZ))
    assert sp["activity"] == 3 and since == 75
    # Dimanche 07:00 : avant le premier changement, le dernier de la semaine précédente.
    sp, _ = p.current_switch(WEEK, datetime(2026, 11, 1, 7, 0, tzinfo=TZ))
    assert sp["time"] == "22:00" and sp["day"] == 2
    assert p.current_switch([], datetime(2026, 11, 3, tzinfo=TZ)) == (None, 0)


def test_learn():
    acts = {}
    at = datetime(2026, 11, 3, 10, 15, tzinfo=TZ)
    # En cours : Absent (16 °C) ; prochain changement 17:30 : Soir (19 °C).
    assert p.learn(acts, WEEK, "schedule", 16.0, 19.0, at) == [(3, None), (5, None)]
    assert acts[3] == {"setpoint": 16.0, "origin": "relevée", "at": int(at.timestamp())}
    assert acts[5]["setpoint"] == 19.0
    # Déjà connues : rien ne change.
    assert p.learn(acts, WEEK, "schedule", 16.0, 19.0, at) == []
    # Modifiée sur le thermostat : l'ancienne valeur est rendue.
    assert p.learn(acts, WEEK, "schedule", 16.5, None, at) == [(3, 16.0)]
    # Pas de relevé : hors programme, juste après un changement, consigne propre au changement.
    assert p.learn(acts, WEEK, "override", 21.0, 19.0, at) == []
    assert p.learn(acts, WEEK, "schedule", 19.0, 20.0, datetime(2026, 11, 3, 9, 2, tzinfo=TZ)) == []
    assert p.learn(acts, WEEK, "schedule", 17.0, None, datetime(2026, 11, 3, 23, 0, tzinfo=TZ)) == []
    # Mardi 23:00, prochain changement dimanche 08:00 : Maison.
    assert p.learn(acts, WEEK, "schedule", None, 19.5, datetime(2026, 11, 3, 23, 0, tzinfo=TZ)) == [(2, None)]
    # Une saisie est remplacée par le relevé.
    acts[5] = {"setpoint": 20.0, "origin": "saisie"}
    assert p.learn(acts, WEEK, "schedule", 19.0, None, datetime(2026, 11, 3, 18, 0, tzinfo=TZ)) == [(5, 20.0)]


def test_next_switch():
    assert p.next_switch(WEEK, datetime(2026, 11, 3, 10, 15, tzinfo=TZ))["time"] == "17:30"
    # Après le dernier changement de la semaine : le premier (dimanche 08:00).
    assert p.next_switch(WEEK, datetime(2026, 11, 7, 12, 0, tzinfo=TZ))["day"] == 0


def test_public_and_storage():
    acts = p.load_activities(p.dump_activities({2: {"setpoint": 19.0, "origin": "saisie"}}))
    out = p.public(acts, [WEEK, [{"day": 1, "time": "07:00", "activity": 7, "setpoint": None}]])
    assert [a["n"] for a in out] == [1, 2, 3, 4, 5, 7]
    assert out[1] == {"n": 2, "name": "Maison", "setpoint": 19.0, "origin": "saisie"}
    assert out[-1]["name"] == "Activité 7" and out[-1]["setpoint"] is None
    assert p.load_activities("pas du json") == {}


def test_validate_week():
    import pytest

    ok = p.validate_week(list(reversed(WEEK)), {1, 2, 3, 4, 5})
    assert ok == sorted(WEEK, key=lambda sp: (sp["day"], sp["time"]))
    bad = [
        ([{"day": 7, "time": "06:00", "activity": 1}], "jour invalide"),
        ([{"day": 1, "time": "6:00", "activity": 1}], "heure invalide"),
        ([{"day": 1, "time": "24:00", "activity": 1}], "heure invalide"),
        (
            [{"day": 1, "time": "06:00", "activity": 1}, {"day": 1, "time": "06:00", "activity": 2}],
            "deux créneaux à 06:00 le lundi",
        ),
        ([{"day": 1, "time": "06:00", "activity": 9}], "activité inconnue"),
        ([], "au moins un créneau"),
        ([{"day": 2, "time": f"0{h}:00", "activity": 1} for h in range(7)], "6 créneaux au plus par jour (mardi)"),
    ]
    for week, msg in bad:
        with pytest.raises(ValueError, match=msg.replace("(", r"\(").replace(")", r"\)")):
            p.validate_week(week, {1, 2, 3, 4, 5})
    # Eau chaude : confort ou non, pas d'activité.
    assert p.validate_week([{"day": 0, "time": "07:00", "comfort": 1}]) == [
        {"day": 0, "time": "07:00", "comfort": True}
    ]


def test_changed_days_and_label():
    new = [dict(sp) for sp in WEEK]
    new[2]["time"] = "06:40"
    assert p.changed_days(WEEK, new) == [2]
    assert p.days_label([0, 1, 2]) == "lundi, mardi, dimanche"
    assert p.days_label(list(range(7))) == "toute la semaine"
