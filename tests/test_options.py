"""Options sur une entrée chargée : enregistrées puis rechargement automatique."""

from homeassistant.config_entries import ConfigEntryState
from homeassistant.data_entry_flow import FlowResultType


async def test_options_flow_on_loaded_entry_reloads(hass, config_entry, mock_client):
    # Entrée chargée : HA refuse OptionsFlowWithReload si un écouteur de mise à jour est posé.
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"default_duration": "next", "emitter": "floor", "connection": "direct", "max_floor_flow": 45,
         "base_temp": -15, "gas_price": 0.15},
    )  # fmt: skip
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert config_entry.options["gas_price"] == 0.15
    assert config_entry.state is ConfigEntryState.LOADED
