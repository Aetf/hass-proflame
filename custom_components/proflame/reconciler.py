"""Periodically say the believed state again, so the appliance matches it.

Every other part of this integration transmits when something *changes*. That
is not enough on its own, because the appliance can drift away from the belief
without anything changing here: a frame the air carried but the appliance did
not act on, a handset press while the receiver was down, a mains interruption.
None of it can be detected — the appliance answers no questions — so the only
available repair is to say the whole state again and let the next frame carry
it.

Timed from the last time anything was successfully transmitted rather than on a
fixed cadence, because a fireplace being actively used is already being told
its state repeatedly. There is nothing to repair a minute after a command
landed, and re-asserting then is air time on a shared half-duplex radio spent
saying something that was just said.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
import logging

from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import (
    async_track_point_in_utc_time,
    async_track_state_change_event,
)
from homeassistant.util import dt as dt_util

from .device import Change, ProflameDevice

_LOGGER = logging.getLogger(__name__)


class ProflameReconciler:
    """Re-asserts the believed state once the radio has been quiet long enough."""

    def __init__(
        self, hass: HomeAssistant, device: ProflameDevice, interval: timedelta
    ) -> None:
        """Initialize the reconciler."""
        self.hass = hass
        self.device = device
        self.interval = interval
        self._cancel_timer: Callable[[], None] | None = None
        self._running = False

    @callback
    def async_start(self) -> Callable[[], None]:
        """Begin reconciling, returning the callback that stops it."""
        unsubscribers = [
            # Any transmission, successful or not, moves the deadline.
            self.device.async_add_listener(self._handle_device_update),
            # And a radio coming back is the moment to catch up, rather than
            # whenever a timer that has been firing into a dead transmitter
            # happens to come round again.
            async_track_state_change_event(
                self.hass, [self.device.transmitter], self._handle_transmitter
            ),
        ]
        self._async_schedule()

        @callback
        def stop() -> None:
            for unsubscribe in unsubscribers:
                unsubscribe()
            self._async_cancel()

        return stop

    @callback
    def _handle_device_update(self, _change: Change) -> None:
        self._async_schedule()

    @callback
    def _handle_transmitter(self, _event: object) -> None:
        self._async_schedule()

    @property
    def _transmitter_usable(self) -> bool:
        state = self.hass.states.get(self.device.transmitter)
        return state is not None and state.state != STATE_UNAVAILABLE

    @callback
    def _async_cancel(self) -> None:
        if self._cancel_timer is not None:
            self._cancel_timer()
            self._cancel_timer = None

    @callback
    def _async_deadline(self) -> datetime | None:
        """When the state should next be re-asserted, or `None` for now.

        Counted from the last *attempt* rather than strictly from the last
        success. Counting only successes would spin: a failed reconcile leaves
        the last success where it was, so its deadline stays in the past and
        the retry is immediate, forever, against a radio that is plainly not
        working.

        `None` means nothing has been transmitted at all in this run of Home
        Assistant, which is the case with the most to repair — the handset may
        have been used throughout a restart with nothing listening — so it does
        not wait an interval to find out.
        """
        attempts = [at for at in (self.device.last_success, self.device.last_failure) if at]
        if not attempts:
            return None
        return max(attempts) + self.interval

    @callback
    def _async_schedule(self) -> None:
        """Set the timer for the next re-assertion, replacing any pending one."""
        self._async_cancel()
        if not self.interval or self._running:
            return
        if not self._transmitter_usable:
            # Nothing to schedule against a radio that cannot transmit. The
            # state listener above brings this back when the radio does.
            return

        deadline = self._async_deadline()
        now = dt_util.utcnow()
        if deadline is None or deadline <= now:
            self.hass.async_create_task(self._async_reconcile())
            return
        self._cancel_timer = async_track_point_in_utc_time(
            self.hass, self._async_fire, deadline
        )

    async def _async_fire(self, _now: datetime) -> None:
        self._cancel_timer = None
        await self._async_reconcile()

    async def _async_reconcile(self) -> None:
        """Re-assert the state, then arrange the next one either way."""
        if self._running:
            return
        self._running = True
        try:
            await self.device.async_reconcile()
        except HomeAssistantError as err:
            # Counted and timestamped by the device already; this is only to
            # stop it escaping into the event loop as an unhandled task.
            _LOGGER.debug("reconcile could not be transmitted: %s", err)
        finally:
            self._running = False
        self._async_schedule()
