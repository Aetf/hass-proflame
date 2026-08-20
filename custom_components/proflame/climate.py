"""A thermostat that runs in Home Assistant rather than in the handset.

The appliance has a thermostat mode of its own, driven by a sensor inside the
handset. It is not exposed here: switching it on hands control back to the
handset, which is the opposite of asking Home Assistant to do something. This
entity does the regulating instead, from a temperature source Home Assistant
can actually read — which can be any sensor in the house, including a better
placed one than the handset's.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, override

from homeassistant.components.climate import (
    FAN_OFF,
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import (
    ATTR_TEMPERATURE,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    UnitOfTemperature,
)
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.helpers.restore_state import ExtraStoredData, RestoreEntity

from . import ProflameConfigEntry
from .const import CONF_TEMPERATURE_SENSOR
from .device import Change, Origin
from .entity import ProflameEntity
from .protocol import MAX_LEVEL

_LOGGER = logging.getLogger(__name__)

#: How often the control law is evaluated. Deliberately unhurried: the room is
#: slow and the radio is shared.
_EVALUATE_EVERY = timedelta(minutes=1)

#: The shortest gap between two transmissions from this entity. Every
#: adjustment is roughly a second of air time on a half-duplex radio that is
#: deaf while it transmits, so a thermostat that chased every fluctuation would
#: spend its life talking and never listen.
_MIN_INTERVAL = timedelta(minutes=5)

#: How far below target the room must fall before the fire is lit, and how far
#: above before it goes out. Without it the fire would cycle on the noise of
#: the sensor's last digit.
_HYSTERESIS = 0.3

#: Degrees of deficit per flame level. Three degrees below target asks for
#: everything the appliance has.
_DEGREES_PER_LEVEL = 0.5


@dataclass
class ThermostatData(ExtraStoredData):
    """What the thermostat has to remember across a restart.

    The mode and the target are the request, and losing them silently ends
    heating while leaving whatever was lit burning unmanaged. The yield goes
    with them: restoring a thermostat to `heat` without remembering that
    somebody had taken the flame off it would have it resume at the next tick
    and override a level a person had set by hand, which is the exact surprise
    the yield exists to prevent.
    """

    hvac_mode: str
    target_temperature: float | None
    suspended: bool

    @override
    def as_dict(self) -> dict[str, Any]:
        """Serialize."""
        return {
            "hvac_mode": self.hvac_mode,
            "target_temperature": self.target_temperature,
            "suspended": self.suspended,
        }


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ProflameConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the thermostat, if a temperature source was configured."""
    sensor = entry.options.get(CONF_TEMPERATURE_SENSOR)
    if not sensor:
        # No entity at all rather than one that cannot regulate: a thermostat
        # with nothing to read is furniture.
        return
    async_add_entities([ProflameThermostat(entry.runtime_data, sensor)])


class ProflameThermostat(ProflameEntity, ClimateEntity, RestoreEntity):
    """Regulates the flame from an external temperature sensor."""

    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT]
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.FAN_MODE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )
    # The blower belongs on a thermostat: it is part of getting heat into the
    # room. The accent light does not, and has no place here — it is not
    # climate, and there is a light entity for it.
    _attr_fan_modes = [FAN_OFF, *(str(level) for level in range(1, MAX_LEVEL + 1))]
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_min_temp = 5
    _attr_max_temp = 30
    _attr_target_temperature_step = 0.5

    def __init__(self, device: Any, sensor: str) -> None:
        """Initialize the thermostat."""
        super().__init__(device, "thermostat")
        self._sensor = sensor
        self._attr_hvac_mode = HVACMode.OFF
        self._attr_target_temperature = 20.0
        self._last_command: Any = None
        #: Somebody else has the flame. The *mode* still says heat, because
        #: that is what was asked for and it has not been withdrawn; this is
        #: what the appliance is actually doing about it, which is nothing.
        self._suspended = False
        self._unregister: Any = None

    @property
    @override
    def current_temperature(self) -> float | None:
        """Whatever the configured sensor says."""
        state = self.hass.states.get(self._sensor)
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return None
        try:
            return float(state.state)
        except ValueError:
            return None

    @property
    @override
    def hvac_action(self) -> HVACAction:
        """What is actually happening, as opposed to what was asked for.

        While suspended this is `off` even though the mode is `heat`: the
        request stands, the thermostat is simply not the one driving. That
        split is the whole point of having both — and without it, yielding had
        to be faked as a mode change, which then dragged the fire out with it.
        """
        if self._attr_hvac_mode is HVACMode.OFF or self._suspended:
            return HVACAction.OFF
        return HVACAction.HEATING if self.device.state.power else HVACAction.IDLE

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any]:
        """Say plainly whether the thermostat is in control right now."""
        return {"thermostat_in_control": not self._suspended}

    @callback
    def _sync_manager(self) -> None:
        """Declare, or stop declaring, that this is driving the appliance.

        Registered only while actually regulating — heating and not yielded —
        because the flag is what tells the auto-off timer whether a fire that
        is currently out is idle or finished.
        """
        # Registered whenever the request stands, yielded or not. A yielded
        # thermostat is not driving, but it is still something that has to be
        # told when heating is over — otherwise an expiring timer cannot reach
        # it and the next resume relights the fire it just put out.
        holding = self._attr_hvac_mode is HVACMode.HEAT
        if holding and self._unregister is None:
            self._unregister = self.device.async_register_manager(
                "thermostat",
                self._stop_regulating,
                lambda: self._attr_hvac_mode is HVACMode.HEAT and not self._suspended,
            )
        elif not holding and self._unregister is not None:
            self._unregister()
            self._unregister = None

    @callback
    def _stop_regulating(self) -> None:
        """Give up control because something told everything to stop.

        The mode goes to off because this really is a withdrawal of the
        request, unlike yielding the flame — the timer expired, and heating is
        over until somebody asks again.
        """
        _LOGGER.info("told to stop; the thermostat is standing down")
        self._attr_hvac_mode = HVACMode.OFF
        self._unregister = None
        self.async_write_ha_state()

    @override
    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Start or stop regulating.

        Asking for off here *does* put the fire out — somebody said stop
        heating, and leaving a gas fire burning after being told to stop is
        not a defensible reading of that. What must not put the fire out is
        yielding the flame to someone else, which is a different event
        entirely and no longer expressed as a mode change; see
        [`Self._handle_device_update`].

        Selecting heat also resumes after a yield, since choosing it again is
        as clear a statement of "manage this" as there is.
        """
        self._attr_hvac_mode = hvac_mode
        self._suspended = False
        # Before touching the appliance, not after. Anything watching for a
        # fire that goes out has to be able to tell "the thermostat is idling"
        # from "nobody is driving this", and the first thing a fresh heat mode
        # may do is turn the fire off because the room is already warm.
        self._sync_manager()
        if hvac_mode is HVACMode.OFF:
            if self.device.state.power:
                await self.device.async_set(Origin.THERMOSTAT, power=False)
        else:
            await self._async_regulate(force=True)
        self.async_write_ha_state()

    @property
    @override
    def fan_mode(self) -> str:
        """The blower speed, as the thermostat sees it."""
        level = self.device.state.fan
        return FAN_OFF if level == 0 else str(level)

    @override
    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Set the blower speed.

        Passed straight through rather than regulated: the blower moves heat
        that is already there, so how hard it should run is a preference
        rather than something to solve for.
        """
        fan = 0 if fan_mode == FAN_OFF else int(fan_mode)
        await self.device.async_set(Origin.THERMOSTAT, fan=fan)

    @override
    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set the target, and act on it now rather than at the next tick."""
        if (target := kwargs.get(ATTR_TEMPERATURE)) is None:
            return
        self._attr_target_temperature = float(target)
        # Setting a target is engaging with the thermostat, so it resumes.
        self._suspended = False
        self._sync_manager()
        if self._attr_hvac_mode is HVACMode.HEAT:
            await self._async_regulate(force=True)
        self.async_write_ha_state()

    @property
    @override
    def extra_restore_state_data(self) -> ThermostatData:
        """What to write down before shutting."""
        return ThermostatData(
            hvac_mode=str(self._attr_hvac_mode),
            target_temperature=self._attr_target_temperature,
            suspended=self._suspended,
        )

    @override
    async def async_added_to_hass(self) -> None:
        """Restore the request, then watch the clock, the sensor and the appliance."""
        await super().async_added_to_hass()
        await self._async_restore()

        @callback
        def sensor_changed(_event: Event[EventStateChangedData]) -> None:
            self.async_write_ha_state()

        self.async_on_remove(
            async_track_state_change_event(self.hass, [self._sensor], sensor_changed)
        )
        self.async_on_remove(
            async_track_time_interval(self.hass, self._async_tick, _EVALUATE_EVERY)
        )

        @callback
        def deregister() -> None:
            if self._unregister is not None:
                self._unregister()
                self._unregister = None

        self.async_on_remove(deregister)

    async def _async_restore(self) -> None:
        """Pick up the request this thermostat was holding when it stopped.

        Nothing is transmitted here. If the room needs heat the next tick says
        so a minute later, and starting up is already noisy enough on a shared
        radio — the reconciler transmits at startup as it is.
        """
        if (stored := await self.async_get_last_extra_data()) is None:
            return
        data = stored.as_dict()
        try:
            self._attr_hvac_mode = HVACMode(data["hvac_mode"])
            target = data["target_temperature"]
            self._attr_target_temperature = None if target is None else float(target)
            self._suspended = bool(data["suspended"])
        except (KeyError, TypeError, ValueError) as err:
            _LOGGER.warning("ignoring unusable stored thermostat state: %s", err)
            return
        _LOGGER.info(
            "restored: mode %s, target %s, %s",
            self._attr_hvac_mode,
            self._attr_target_temperature,
            "yielded" if self._suspended else "in control",
        )
        # Before anything can ask, so that a restored request is visible to the
        # auto-off timer from the moment it exists rather than from the first
        # time this regulates.
        self._sync_manager()

    async def _async_tick(self, _now: Any) -> None:
        if self._attr_hvac_mode is HVACMode.HEAT and not self._suspended:
            try:
                await self._async_regulate()
            except HomeAssistantError as err:
                # A control loop is the one caller with nobody to raise at. The
                # device has already counted and timestamped this, and the
                # diagnostic sensors are where it belongs; letting it escape
                # here only produces an unhandled-task traceback once a minute
                # for as long as the radio is out.
                _LOGGER.warning("could not regulate: %s", err)

    @callback
    def _handle_device_update(self, change: Change) -> None:
        """Stand down when somebody else drives the appliance.

        Which somebody is carried on the change rather than deduced from it.
        Deducing it was the source of every bug this integration has had: a
        comparison against the level last asked for cannot tell this
        thermostat's own command from an identical one by somebody else, and
        sees nothing at all in a field it does not compare — which is how
        switching the fire off walked past a check that only watched the flame.

        Its own commands, a reconciler re-asserting what is already believed,
        and the state being restored are all changes this must ignore.
        """
        mine = (Origin.THERMOSTAT, Origin.RECONCILE, Origin.RESTORE)
        if self._attr_hvac_mode is HVACMode.HEAT and change.origin not in mine:
            if change.moved("power"):
                # Switching the fire withdraws the request rather than handing
                # over the flame: off says the fireplace is finished with, on
                # says it is wanted whatever this thinks. Without it a cold
                # room would relight, a minute later, a fire somebody had just
                # switched off by hand.
                _LOGGER.info(
                    "the fire was switched %s by %s; standing down",
                    "on" if change.current.power else "off",
                    change.origin,
                )
                self._attr_hvac_mode = HVACMode.OFF
                self._suspended = False
                self._sync_manager()
            elif change.moved("flame") and not self._suspended:
                # The flame moved and the fire did not: somebody is choosing
                # the level, not ending the heating. A handover.
                _LOGGER.info(
                    "flame moved to %d by %s; yielding control and leaving the "
                    "fire alone (select heat again to resume)",
                    change.current.flame,
                    change.origin,
                )
                self._suspended = True
                self._sync_manager()
        self.async_write_ha_state()

    async def _async_regulate(self, *, force: bool = False) -> None:
        """Decide what the flame should be, and say so if it has changed."""
        current = self.current_temperature
        if current is None or self._attr_target_temperature is None:
            return

        deficit = self._attr_target_temperature - current
        if self.device.state.power and deficit <= 0:
            wanted_power, wanted_flame = False, self.device.state.flame
        elif not self.device.state.power and deficit < _HYSTERESIS:
            return  # cold enough to leave alone, and already out
        else:
            wanted_power = True
            wanted_flame = max(1, min(MAX_LEVEL, round(deficit / _DEGREES_PER_LEVEL)))

        if (
            wanted_power == self.device.state.power
            and wanted_flame == self.device.state.flame
        ):
            return

        now = self.hass.loop.time()
        if (
            not force
            and self._last_command is not None
            and now - self._last_command < _MIN_INTERVAL.total_seconds()
        ):
            return

        self._last_command = now
        _LOGGER.debug(
            "%.1f°C against a target of %.1f: power=%s flame=%d",
            current,
            self._attr_target_temperature,
            wanted_power,
            wanted_flame,
        )
        await self.device.async_set(Origin.THERMOSTAT, power=wanted_power, flame=wanted_flame)
