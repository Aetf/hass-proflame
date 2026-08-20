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
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
import logging
from typing import Any

from homeassistant.components.radio_frequency import async_send_command
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .protocol import ProflameCommand, Remote, State, decode_frame

_LOGGER = logging.getLogger(__name__)

_STORE_VERSION = 1


class Origin(StrEnum):
    """What caused a change to the believed state.

    Carried rather than inferred. Every bug this integration has had came from
    working out who had caused a change by comparing values, which cannot tell
    "I set it to 4" from "somebody else set it to 4" and is blind to any field
    the comparison does not happen to include. See docs/STATE.md.
    """

    #: One of the direct manual controls: the switch, flame, blower, light or
    #: pilot. Not split further, because nothing downstream needs to know which
    #: one a person reached for, and a distinction nobody consumes will rot.
    USER = "user"
    #: The thermostat, whether regulating or acting on its own controls.
    THERMOSTAT = "thermostat"
    #: The auto-off deadline.
    TIMER = "timer"
    #: A frame decoded off the air.
    HANDSET = "handset"
    #: Loaded from storage at startup. Believed, never transmitted.
    RESTORE = "restore"
    #: The reconciler re-asserting what was already believed.
    RECONCILE = "reconcile"


@dataclass(frozen=True, slots=True)
class Change:
    """One transition of the believed state, and where it came from."""

    origin: Origin
    previous: State
    current: State

    def moved(self, field: str) -> bool:
        """Whether one field differs across this change."""
        return getattr(self.previous, field) != getattr(self.current, field)


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
        #: When the auto-off timer should fire, if one is armed. Persisted so
        #: a restart cannot quietly drop it and leave the fire burning.
        self.auto_off_at: datetime | None = None
        #: The last transmission the radio accepted, the last that it did not,
        #: and how many have failed. Deliberately not persisted: after a restart
        #: nothing has been said to the appliance yet, which is exactly the
        #: state the reconciler exists to repair.
        self.last_success: datetime | None = None
        self.last_failure: datetime | None = None
        self.last_error: str | None = None
        self.failures = 0
        self._listeners: list[Callable[[Change], None]] = []
        #: Anything holding a standing request on the appliance, by name, with
        #: the callback that withdraws it and a predicate saying whether it is
        #: currently the one acting.
        #:
        #: The two are not the same and conflating them breaks something either
        #: way. A yielded thermostat holds its request but is not driving: it
        #: must still be reachable when heating is called off, or an expiring
        #: timer cannot stop it and the next resume relights the fire. And a
        #: thermostat that has merely reached temperature *is* driving even
        #: though the fire is out, or a timer would vanish the moment the room
        #: got warm.
        self._managers: dict[str, tuple[Callable[[], None], Callable[[], bool]]] = {}
        self._store = Store[dict[str, Any]](hass, _STORE_VERSION, f"{entry_id}_state")

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
            if (deadline := stored.get("auto_off_at")) is not None:
                self.auto_off_at = dt_util.parse_datetime(deadline)

    @callback
    def async_add_listener(
        self, listener: Callable[[Change], None]
    ) -> Callable[[], None]:
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
        def handle_frame(frame: dict[str, Any]) -> None:
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
            self.hass.async_create_task(
                self._async_adopt(decoded.state, Origin.HANDSET)
            )

        return async_dispatcher_connect(
            self.hass, SIGNAL_RX_FRAME.format(transmitter_entry_id), handle_frame
        )

    @property
    def managed(self) -> bool:
        """Whether something is *acting on* the appliance by itself right now.

        A fire that is out but managed is idle, not finished — which is the
        difference between the thermostat pausing and somebody switching the
        fireplace off. A holder that has yielded does not count: it is waiting,
        not acting.
        """
        return any(driving() for _, driving in self._managers.values())

    @callback
    def async_register_manager(
        self,
        name: str,
        stop: Callable[[], None],
        driving: Callable[[], bool],
    ) -> Callable[[], None]:
        """Declare a standing request, how to withdraw it, and whether it acts."""
        self._managers[name] = (stop, driving)
        self._notify()

        def unregister() -> None:
            if self._managers.pop(name, None) is not None:
                self._notify()

        return unregister

    async def async_reconcile(self) -> None:
        """Say the believed state again, so the appliance matches it.

        Nothing keeps the *appliance* correct on its own — a command the air
        carried but the appliance did not act on, a handset press during a
        receiver outage, a mains cut — and it cannot be asked. Saying it again
        is the only way to close a gap that has already opened.

        Re-asserting is not symmetric, and that asymmetry is the reason this is
        defensible. `power = off` can only ever extinguish, and the error it
        corrects is a fire burning that Home Assistant believes is out.
        `power = on` can relight a gas appliance somebody deliberately put out,
        if the belief has gone stale. What bounds that is the receiver: a
        transmitter that can reconcile is also listening, so a stale belief
        needs a radio in range of the fireplace but not of the handset. See
        docs/STATE.md.
        """
        _LOGGER.info("reconciling: re-asserting %s", self.state)
        await self.async_set(Origin.RECONCILE, thermostat=self.state.thermostat)

    async def async_shut_down(self) -> None:
        """Stop everything driving the appliance, then put the fire out.

        Turning the fire off is not enough on its own: whatever was managing it
        would simply light it again. Anything that means "stop heating" rather
        than "off for now" has to come through here.
        """
        for stop, _ in list(self._managers.values()):
            stop()
        self._managers.clear()
        self._notify()
        if self.state.power:
            await self.async_set(Origin.TIMER, power=False)

    async def async_set(self, origin: Origin, **changes: object) -> None:
        """Change some fields and transmit the whole resulting state.

        Every command clears the thermostat bit *by default*. Home Assistant
        driving the appliance and the handset's own thermostat driving it are
        two controllers fighting over the same flame; if a thermostat is wanted
        here, it belongs in Home Assistant with a temperature source it can
        actually read.

        By default, and not unconditionally, because the reconciler has to be
        able to say the believed state verbatim. Clearing the bit there would
        make re-asserting a belief change it — the handset's own thermostat
        mode, faithfully followed off the air, would be switched off a quarter
        of an hour later by something whose whole purpose is to change nothing.
        """
        target = self.state.evolve(**{"thermostat": False, **changes})
        command = ProflameCommand(self.remote, target, frequency=self.frequency)

        # At info, and naming what changed. Several things can command this
        # appliance — the switch, the slider, the thermostat, a timer — and
        # when one of them does something surprising, the first question is
        # always which one, and the log has to be able to answer it.
        changed = ", ".join(f"{name}={value}" for name, value in sorted(changes.items()))
        _LOGGER.info("commanding %s from %s (changed: %s)", target, origin, changed or "nothing")
        try:
            await async_send_command(self.hass, self.transmitter, command)
        except Exception as err:
            # A radio that has been refusing everything for an hour looks
            # exactly like a fireplace nobody has touched, so failures are
            # counted rather than only raised. The belief is left untouched:
            # nothing was said, so nothing changed.
            self.failures += 1
            self.last_failure = dt_util.utcnow()
            self.last_error = str(err) or type(err).__name__
            _LOGGER.warning("transmission failed (%d so far): %s", self.failures, err)
            self._notify()
            raise

        self.last_success = dt_util.utcnow()
        await self._async_adopt(target, origin)

    async def async_set_auto_off(self, deadline: datetime | None) -> None:
        """Remember when the fire should be put out, or that it should not."""
        self.auto_off_at = deadline
        await self._async_save()

    async def _async_adopt(self, state: State, origin: Origin) -> None:
        """Believe this state, remember it, and tell the entities why."""
        change = Change(origin=origin, previous=self.state, current=state)
        self.state = state
        await self._async_save()
        self._notify(change)

    async def _async_save(self) -> None:
        state = self.state
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
                "auto_off_at": self.auto_off_at.isoformat() if self.auto_off_at else None,
            }
        )

    @callback
    def _notify(self, change: Change | None = None) -> None:
        """Tell the entities what happened.

        A change with no transition — a manager registering, say — reports
        itself as a reconcile of the current state, since that is exactly what
        it is from a listener's point of view: nothing moved.
        """
        if change is None:
            change = Change(Origin.RECONCILE, self.state, self.state)
        for listener in list(self._listeners):
            listener(change)
