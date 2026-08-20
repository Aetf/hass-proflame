"""Pilot ignition mode, and the auto-off timer."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, override

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.util import dt as dt_util

from . import ProflameConfigEntry
from .const import AUTO_OFF_NONE, AUTO_OFF_OPTIONS
from .device import Origin
from .entity import ProflameEntity

_LOGGER = logging.getLogger(__name__)

CONTINUOUS = "continuous"
INTERMITTENT = "intermittent"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ProflameConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the selects."""
    device = entry.runtime_data
    async_add_entities([ProflamePilotMode(device), ProflameAutoOff(device)])


class ProflamePilotMode(ProflameEntity, SelectEntity):
    """Continuous (CPI) or intermittent (IPI) pilot ignition.

    A select rather than a switch, because neither position is "off": both are
    working modes, and a switch would make one of them look like the absence
    of the other. It is a setting about how the appliance behaves between
    uses, not a control, so it sits under the device's configuration section.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = [CONTINUOUS, INTERMITTENT]

    def __init__(self, device: Any) -> None:
        """Initialize the select."""
        super().__init__(device, "pilot_mode")

    @property
    @override
    def current_option(self) -> str:
        """The pilot mode believed current."""
        return CONTINUOUS if self.device.state.pilot else INTERMITTENT

    @override
    async def async_select_option(self, option: str) -> None:
        """Switch the pilot mode."""
        await self.device.async_set(Origin.USER, pilot=option == CONTINUOUS)


class ProflameAutoOff(ProflameEntity, SelectEntity):
    """Put the fire out after a while, if it is still burning.

    Deliberately the *only* safety feature here, and deliberately dumb. Whether
    the fire should be lit when nobody is home, or at night, or when a window
    is open, depends on things this integration has no business knowing —
    those belong in an automation, where the conditions live. A timer needs
    nothing but the fire itself.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = [AUTO_OFF_NONE, *AUTO_OFF_OPTIONS]

    def __init__(self, device: Any) -> None:
        """Initialize the select."""
        super().__init__(device, "auto_off")
        self._option = AUTO_OFF_NONE
        self._cancel: Any = None

    @property
    @override
    def current_option(self) -> str:
        """The armed duration, or none."""
        return self._option

    @override
    async def async_select_option(self, option: str) -> None:
        """Arm or disarm the timer."""
        self._disarm()
        self._option = option
        if option != AUTO_OFF_NONE:
            await self._arm(dt_util.utcnow() + timedelta(minutes=AUTO_OFF_OPTIONS[option]))
        else:
            await self.device.async_set_auto_off(None)
        self.async_write_ha_state()

    async def _arm(self, deadline: datetime) -> None:
        """Schedule the turn-off, and remember it across restarts.

        Persisted on purpose: a timer that a restart silently forgets would
        leave the fire burning exactly when the thing meant to stop it is
        gone.
        """
        self._cancel = async_track_point_in_utc_time(self.hass, self._fired, deadline)
        await self.device.async_set_auto_off(deadline)

    @callback
    def _disarm(self) -> None:
        if self._cancel is not None:
            self._cancel()
            self._cancel = None

    async def _fired(self, _now: datetime) -> None:
        """Time is up: stop everything heating, and disarm.

        Not merely "turn the fire off". A thermostat that was regulating would
        find the fire out, decide the room was still cold, and light it again
        within the minute — leaving a safety feature that a thermostat silently
        overrules.
        """
        _LOGGER.info("auto-off reached; shutting the fireplace down")
        self._cancel = None
        try:
            await self.device.async_shut_down()
        except HomeAssistantError as err:
            # Disarming first would be the worst of both: the radio is out, the
            # fire is still lit, and the one thing meant to put it out has
            # deleted itself. So the deadline stands, in the past, and
            # [`Self._transmitter_changed`] tries again when there is a radio.
            _LOGGER.warning("auto-off could not be transmitted, still armed: %s", err)
            self.async_write_ha_state()
            return
        self._option = AUTO_OFF_NONE
        await self.device.async_set_auto_off(None)
        self.async_write_ha_state()

    @override
    @callback
    def _transmitter_changed(self, event: object) -> None:
        """Retry an expiry that the radio was not there for."""
        super()._transmitter_changed(event)
        deadline = self.device.auto_off_at
        if deadline is not None and deadline <= dt_util.utcnow() and self.available:
            self.hass.async_create_task(self._fired(dt_util.utcnow()))

    @override
    async def async_added_to_hass(self) -> None:
        """Re-arm a timer that outlived a restart, and follow the fire."""
        await super().async_added_to_hass()

        deadline = self.device.auto_off_at
        if deadline is not None:
            if deadline <= dt_util.utcnow():
                # The deadline passed while Home Assistant was down. Act on it
                # rather than dropping it: the point of the timer is that
                # something eventually puts the fire out.
                await self._fired(dt_util.utcnow())
            else:
                self._option = _closest_option(deadline)
                self._cancel = async_track_point_in_utc_time(
                    self.hass, self._fired, deadline
                )

        self.async_on_remove(self._disarm)
        self.async_on_remove(self.device.async_add_listener(self._fire_state_changed))

    @callback
    def _fire_state_changed(self, _change: Any) -> None:
        """Disarm when the fireplace is finished with, not merely idle.

        A timer left armed after the fire was already out would put out
        whatever was lit next, which is worse than useless. But "the fire is
        out" is not the same as "somebody is done with it": a thermostat that
        has reached temperature cycles the fire off constantly, and disarming
        there would quietly delete a timer the moment the room got warm.

        So it disarms when the fire is out *and* nothing is driving it.
        """
        finished = not self.device.state.power and not self.device.managed
        if self._option != AUTO_OFF_NONE and finished:
            self._disarm()
            self._option = AUTO_OFF_NONE
            self.hass.async_create_task(self.device.async_set_auto_off(None))
        self.async_write_ha_state()


def _closest_option(deadline: datetime) -> str:
    """Name the armed duration for a restored deadline."""
    remaining = (deadline - dt_util.utcnow()).total_seconds() / 60
    best = min(AUTO_OFF_OPTIONS, key=lambda name: abs(AUTO_OFF_OPTIONS[name] - remaining))
    return best
