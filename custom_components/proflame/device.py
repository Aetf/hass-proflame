"""The state Home Assistant keeps for one fireplace, and how it sends it.

The appliance is stateless: every frame carries the complete state of every
field, it never asks what it is currently doing, and the levels that survive a
power cycle survive in the *handset*, not in the fireplace. So something has to
hold that state on Home Assistant's behalf, and this is it.

The consequence, spelled out in docs/PROTOCOL.md: Home Assistant and the
handset are two state holders that cannot hear each other. This one listens, so
it follows the handset. The handset cannot listen, so after a command from here
it is stale, and the next press on it will transmit that stale state.
"""

from __future__ import annotations

from collections.abc import Callable
import logging

from homeassistant.components.radio_frequency import async_send_command
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.storage import Store

from .protocol import ProflameCommand, Remote, State, decode_frame

_LOGGER = logging.getLogger(__name__)

_STORE_VERSION = 1


class ProflameDevice:
    """Holds the believed appliance state and puts changes on the air."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        transmitter: str,
        remote: Remote,
        frequency: int,
    ) -> None:
        """Initialize the device."""
        self.hass = hass
        self.entry_id = entry_id
        self.transmitter = transmitter
        self.remote = remote
        self.frequency = frequency
        self.state = State()
        self._listeners: list[Callable[[], None]] = []
        self._store = Store[dict[str, int]](hass, _STORE_VERSION, f"{entry_id}_state")

    async def async_load(self) -> None:
        """Restore the state believed current when Home Assistant last ran.

        A guess, and unavoidably so: nothing can be asked. It is a better guess
        than "everything off", which would have the first command turn the
        blower and light off as a side effect of adjusting the flame.
        """
        if (stored := await self._store.async_load()) is not None:
            try:
                self.state = State(
                    power=bool(stored["power"]),
                    flame=stored["flame"],
                    fan=stored["fan"],
                    light=stored["light"],
                    thermostat=bool(stored["thermostat"]),
                    aux=bool(stored["aux"]),
                    front=bool(stored["front"]),
                    pilot=bool(stored["pilot"]),
                )
            except (KeyError, ValueError) as err:
                _LOGGER.warning("ignoring unusable stored state: %s", err)

    @callback
    def async_add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Subscribe to state changes, returning an unsubscribe."""
        self._listeners.append(listener)

        def unsubscribe() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return unsubscribe

    @callback
    def async_start_listening(self, transmitter_entry_id: str) -> Callable[[], None]:
        """Follow the handset by watching what the receiver hears."""
        from .const import SIGNAL_RX_FRAME  # noqa: PLC0415

        @callback
        def handle_frame(frame: dict) -> None:
            decoded = decode_frame(frame.get("timings", []))
            if decoded is None:
                return
            # Somebody else's fireplace on the same band is not ours to follow.
            if (decoded.remote.serial1, decoded.remote.serial2) != (
                self.remote.serial1,
                self.remote.serial2,
            ):
                return
            if decoded.state == self.state:
                return
            _LOGGER.debug("following the handset: %s", decoded.state)
            # Persisted like any other change. What the handset tells us is
            # every bit as much the believed state as what we sent ourselves,
            # and forgetting it across a restart would leave the next command
            # built on a stale belief.
            self.hass.async_create_task(self._async_adopt(decoded.state))

        return async_dispatcher_connect(
            self.hass, SIGNAL_RX_FRAME.format(transmitter_entry_id), handle_frame
        )

    async def async_set(self, **changes: object) -> None:
        """Change some fields and transmit the whole resulting state.

        Every command clears the thermostat bit. Home Assistant driving the
        appliance and the handset's own thermostat driving it are two
        controllers fighting over the same flame; if a thermostat is wanted
        here, it belongs in Home Assistant with a temperature source it can
        actually read.
        """
        target = self.state.evolve(thermostat=False, **changes)
        command = ProflameCommand(self.remote, target, frequency=self.frequency)

        _LOGGER.debug("transmitting %s", target)
        await async_send_command(self.hass, self.transmitter, command)

        await self._async_adopt(target)

    async def _async_adopt(self, state: State) -> None:
        """Believe this state, remember it, and tell the entities."""
        self.state = state
        await self._store.async_save(
            {
                "power": int(state.power),
                "flame": state.flame,
                "fan": state.fan,
                "light": state.light,
                "thermostat": int(state.thermostat),
                "aux": int(state.aux),
                "front": int(state.front),
                "pilot": int(state.pilot),
            }
        )
        self._notify()

    @callback
    def _notify(self) -> None:
        for listener in list(self._listeners):
            listener()
