"""Shared base for the fireplace's entities."""

from __future__ import annotations

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.event import async_track_state_change_event

from .const import DOMAIN
from .device import ProflameDevice


class ProflameEntity(Entity):
    """An entity backed by the one believed appliance state."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, device: ProflameDevice, key: str) -> None:
        """Initialize the entity."""
        self.device = device
        self._attr_unique_id = f"{device.entry_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device.entry_id)},
            manufacturer="SIT",
            model="Proflame 2",
            name="Fireplace",
        )

    @property
    def available(self) -> bool:
        """Follow the transmitter: without a radio there is no control.

        The appliance itself cannot be asked anything, so this is the only
        availability there is to report.
        """
        state = self.hass.states.get(self.device.transmitter)
        return state is not None and state.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN)

    async def async_added_to_hass(self) -> None:
        """Track the shared state and the transmitter's availability."""
        await super().async_added_to_hass()
        self.async_on_remove(self.device.async_add_listener(self.async_write_ha_state))

        @callback
        def transmitter_changed(_event: object) -> None:
            self.async_write_ha_state()

        self.async_on_remove(
            async_track_state_change_event(
                self.hass, [self.device.transmitter], transmitter_changed
            )
        )
