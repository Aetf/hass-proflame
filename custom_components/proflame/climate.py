"""A thermostat that runs in Home Assistant rather than in the handset.

The appliance has a thermostat mode of its own, driven by a sensor inside the
handset. It is not exposed here: switching it on hands control back to the
handset, which is the opposite of asking Home Assistant to do something. This
entity does the regulating instead, from a temperature source Home Assistant
can actually read — which can be any sensor in the house, including a better
placed one than the handset's.
"""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any, override

from homeassistant.components.climate import (
    FAN_OFF,
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, STATE_UNAVAILABLE, STATE_UNKNOWN, UnitOfTemperature
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval

from . import ProflameConfigEntry
from .const import CONF_TEMPERATURE_SENSOR
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


class ProflameThermostat(ProflameEntity, ClimateEntity):
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
        self._commanded_flame: int | None = None
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
        regulating = self._attr_hvac_mode is HVACMode.HEAT and not self._suspended
        if regulating and self._unregister is None:
            self._unregister = self.device.async_register_manager(
                "thermostat", self._stop_regulating
            )
        elif not regulating and self._unregister is not None:
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
        self._commanded_flame = None
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
        self._commanded_flame = None
        self._suspended = False
        if hvac_mode is HVACMode.OFF:
            if self.device.state.power:
                await self.device.async_set(power=False)
        else:
            await self._async_regulate(force=True)
        self._sync_manager()
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
        await self.device.async_set(fan=0 if fan_mode == FAN_OFF else int(fan_mode))

    @override
    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set the target, and act on it now rather than at the next tick."""
        if (target := kwargs.get(ATTR_TEMPERATURE)) is None:
            return
        self._attr_target_temperature = float(target)
        # Setting a target is engaging with the thermostat, so it resumes.
        self._suspended = False
        if self._attr_hvac_mode is HVACMode.HEAT:
            await self._async_regulate(force=True)
        self._sync_manager()
        self.async_write_ha_state()

    @override
    async def async_added_to_hass(self) -> None:
        """Watch the clock, the sensor, and the appliance."""
        await super().async_added_to_hass()

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

    async def _async_tick(self, _now: Any) -> None:
        if self._attr_hvac_mode is HVACMode.HEAT and not self._suspended:
            await self._async_regulate()

    @callback
    def _handle_device_update(self) -> None:
        """Yield when somebody else takes the flame.

        If the flame is not where this entity put it, someone moved it — the
        handset, an automation, the slider — and two controllers arguing over
        one fire helps nobody.

        Yielding leaves the mode alone and the fire alone. Only the action
        changes, to `off`. Turning the mode off instead, as this used to, said
        something the user never asked for and put the fire out along with it.
        """
        if (
            self._attr_hvac_mode is HVACMode.HEAT
            and not self._suspended
            and self._commanded_flame is not None
            and self.device.state.flame != self._commanded_flame
        ):
            _LOGGER.info(
                "flame moved to %d rather than the %d asked for; yielding control "
                "and leaving the fire alone (select heat again to resume)",
                self.device.state.flame,
                self._commanded_flame,
            )
            self._suspended = True
            self._commanded_flame = None
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
        self._commanded_flame = wanted_flame
        _LOGGER.debug(
            "%.1f°C against a target of %.1f: power=%s flame=%d",
            current,
            self._attr_target_temperature,
            wanted_power,
            wanted_flame,
        )
        await self.device.async_set(power=wanted_power, flame=wanted_flame)
