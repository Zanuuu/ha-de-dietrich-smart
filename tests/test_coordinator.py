"""Coordinateur : relève, backoff, réauthentification, événements dans le journal."""

import copy
from datetime import timedelta

from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.de_dietrich_smart.bdr.models import AuthError, SourceError


async def setup(hass, config_entry):
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    rt = config_entry.runtime_data
    # Le coordinateur ne relève que s'il a des abonnés (les entités, à partir de la tâche 6).
    rt.coordinator.async_add_listener(lambda: None)
    return rt


async def test_poll_ok_sets_data(hass, config_entry, mock_client):
    rt = await setup(hass, config_entry)
    assert config_entry.state is ConfigEntryState.LOADED
    assert rt.coordinator.data.zone.setpoint == 21.0
    mock_client.start.assert_awaited_once()


async def test_failures_back_off(hass, config_entry, mock_client, freezer):
    rt = await setup(hass, config_entry)
    mock_client.fetch_state.side_effect = SourceError("cloud BDR injoignable")
    intervals = []
    for _ in range(3):
        freezer.tick(rt.coordinator.update_interval)
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
        intervals.append(rt.coordinator.update_interval)
    assert intervals == [timedelta(seconds=120), timedelta(seconds=240), timedelta(seconds=480)]
    assert rt.journal.events(1)[0]["message"].startswith("Connexion perdue")
    mock_client.fetch_state.side_effect = None
    freezer.tick(rt.coordinator.update_interval)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert rt.coordinator.update_interval == timedelta(seconds=60)
    assert rt.journal.events(1)[0]["message"] == "Connexion rétablie"


async def test_backoff_capped(hass, config_entry, mock_client, freezer):
    rt = await setup(hass, config_entry)
    mock_client.fetch_state.side_effect = SourceError("x")
    for _ in range(8):
        freezer.tick(rt.coordinator.update_interval)
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
    assert rt.coordinator.update_interval == timedelta(seconds=900)


async def test_auth_error_starts_reauth_once(hass, config_entry, mock_client, freezer):
    rt = await setup(hass, config_entry)
    mock_client.fetch_state.side_effect = AuthError("identifiants De Dietrich refusés par le cloud BDR")
    freezer.tick(timedelta(seconds=60))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    calls = mock_client.fetch_state.await_count
    flows = hass.config_entries.flow.async_progress()
    assert [f["context"]["source"] for f in flows] == [SOURCE_REAUTH]
    for _ in range(5):
        freezer.tick(timedelta(seconds=900))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
    assert mock_client.fetch_state.await_count == calls
    assert len(hass.config_entries.flow.async_progress()) == 1
    assert rt.coordinator.last_update_success is False


async def test_null_room_temp_marks_unavailable_once(hass, config_entry, mock_client, state, freezer):
    rt = await setup(hass, config_entry)
    lost = copy.deepcopy(state)
    lost.zone.room_temp = None
    mock_client.fetch_state.return_value = lost
    for _ in range(2):
        freezer.tick(timedelta(seconds=60))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
    assert mock_client.fetch_state.await_count == 3
    msgs = [e["message"] for e in rt.journal.events(50)]
    assert msgs.count("Température ambiante plus transmise par le cloud") == 1


async def test_unload_closes_client(hass, config_entry, mock_client):
    await setup(hass, config_entry)
    assert await hass.config_entries.async_unload(config_entry.entry_id)
    mock_client.close.assert_awaited_once()
