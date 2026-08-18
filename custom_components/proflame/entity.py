"""Shared base for the fireplace's entities."""

from __future__ import annotations

from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.event import async_track_state_change_event

from .const import DOMAIN
from .device import Change, ProflameDevice


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

        Only `unavailable` counts. A transmitter's *state* is the timestamp of
        the last command it sent, so a working one that has never been asked
        for anything reads `unknown` — treating that as unavailable leaves
        every entity here dead until something has already been transmitted,
        which is a deadlock.
        """
        state = self.hass.states.get(self.device.transmitter)
        return state is not None and state.state != STATE_UNAVAILABLE

    @callback
    def _handle_device_update(self, change: Change) -> None:
        """React to the believed state changing.

        Overridable: most entities only need to redraw, but the thermostat and
        the auto-off timer both have to know *who* caused the change, which is
        why it is carried rather than worked out from the values.
        """
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Track the shared state and the transmitter's availability."""
        await super().async_added_to_hass()
        self.async_on_remove(self.device.async_add_listener(self._handle_device_update))

        self.async_on_remove(
            async_track_state_change_event(
                self.hass, [self.device.transmitter], self._transmitter_changed
            )
        )

    @callback
    def _transmitter_changed(self, _event: object) -> None:
        """React to the radio appearing or going away.

        Overridable for the same reason `_handle_device_update` is: a radio
        coming back is the moment for anything that could not be transmitted to
        try again, and waiting for the next unrelated event to notice is how a
        lost command stays lost.
        """
        self.async_write_ha_state()
