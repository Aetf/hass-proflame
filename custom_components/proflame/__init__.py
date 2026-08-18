"""The SIT Proflame fireplace integration.

The consumer half of the split: it knows the appliance and owns no radio. Any
`radio_frequency` transmitter that reaches the band will do — ours, a Broadlink,
or an ESPHome node with a CC1101 beside the fireplace.
"""

from __future__ import annotations

from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import entity_registry as er

from .const import (
    CONF_FREQUENCY,
    CONF_KEY1,
    CONF_KEY2,
    CONF_RECONCILE_INTERVAL,
    CONF_SERIAL1,
    CONF_SERIAL2,
    CONF_TRANSMITTER,
    CONF_VERSION,
    DEFAULT_RECONCILE_INTERVAL,
    DOMAIN,
)
from .device import ProflameDevice
from .reconciler import ProflameReconciler
from .protocol import FCC_FREQUENCY, Remote

type ProflameConfigEntry = ConfigEntry[ProflameDevice]

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.CLIMATE,
    Platform.FAN,
    Platform.LIGHT,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
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

    _async_remove_stale_entities(hass, entry)

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
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Started after the platforms, so that the first re-assertion has entities
    # to update rather than landing on a device nothing is listening to.
    reconciler = ProflameReconciler(
        hass,
        device,
        timedelta(
            minutes=entry.options.get(
                CONF_RECONCILE_INTERVAL, DEFAULT_RECONCILE_INTERVAL
            )
        ),
    )
    entry.async_on_unload(reconciler.async_start())
    return True


async def _async_reload(hass: HomeAssistant, entry: ProflameConfigEntry) -> None:
    """Reload after options change: the thermostat exists only when a
    temperature source does, so adding one has to create it."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ProflameConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


@callback
def _async_remove_stale_entities(hass: HomeAssistant, entry: ProflameConfigEntry) -> None:
    """Drop entities this integration no longer creates.

    The pilot was a switch before it became a select. Without this its old
    entity lingers in the registry as a permanently unavailable leftover, which
    looks like a fault rather than a rename.
    """
    registry = er.async_get(hass)
    for domain, key in ((Platform.SWITCH, "continuous_pilot"),):
        unique_id = f"{entry.entry_id}_{key}"
        if (entity_id := registry.async_get_entity_id(domain, DOMAIN, unique_id)) is not None:
            registry.async_remove(entity_id)
