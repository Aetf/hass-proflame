"""The SIT Proflame fireplace integration.

The consumer half of the split: it knows the appliance and owns no radio. Any
`radio_frequency` transmitter that reaches the band will do — ours, a Broadlink,
or an ESPHome node with a CC1101 beside the fireplace.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import entity_registry as er

from .const import (
    CONF_FREQUENCY,
    CONF_KEY1,
    CONF_KEY2,
    CONF_SERIAL1,
    CONF_SERIAL2,
    CONF_TRANSMITTER,
    CONF_VERSION,
)
from .device import ProflameDevice
from .protocol import FCC_FREQUENCY, Remote

type ProflameConfigEntry = ConfigEntry[ProflameDevice]

PLATFORMS: list[Platform] = [
    Platform.FAN,
    Platform.LIGHT,
    Platform.NUMBER,
    Platform.SWITCH,
]


async def async_setup_entry(hass: HomeAssistant, entry: ProflameConfigEntry) -> bool:
    """Set up a fireplace from a config entry."""
    registry = er.async_get(hass)
    entity_entry = registry.async_get(entry.data[CONF_TRANSMITTER])
    if entity_entry is None:
        # Stored as a registry id so that renaming the transmitter does not
        # break the link; if it has been deleted there is nothing to send with.
        raise ConfigEntryNotReady("the configured transmitter no longer exists")

    device = ProflameDevice(
        hass,
        entry.entry_id,
        entity_entry.entity_id,
        Remote(
            serial1=entry.data[CONF_SERIAL1],
            serial2=entry.data[CONF_SERIAL2],
            version=entry.data[CONF_VERSION],
            key1=entry.data[CONF_KEY1],
            key2=entry.data[CONF_KEY2],
        ),
        entry.data.get(CONF_FREQUENCY, FCC_FREQUENCY),
    )
    await device.async_load()

    # Follow the handset when it is used. The signal is keyed by the
    # transmitter's own config entry, which is the receiver that hears it.
    if entity_entry.config_entry_id is not None:
        entry.async_on_unload(device.async_start_listening(entity_entry.config_entry_id))

    entry.runtime_data = device
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ProflameConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
