"""Where the frames a radio hears come from.

Home Assistant's ``radio_frequency`` platform has transmitters only, so what a
radio *receives* reaches us through a side channel private to the integration
that owns the radio. Each such channel is one :class:`FrameSource`
implementation, and a source is named by the config entry that owns the radio:
``"<domain>:<config entry id>"``, which is what the config entry stores. The
domain says which implementation applies, the entry id which radio.

Every private contract with another integration's internals lives in this
module, so that there is one place to retire when an upstream receiver
platform exists:

- ``hackrf_proxy``: the dispatcher signal :data:`SIGNAL_RX_FRAME`, formatted
  with the config entry id, on which hass-hackrf-proxy re-broadcasts the
  daemon's ``rx_frame`` payload verbatim: ``{"timings": [...]}``.
- ``esphome``: the native API's ``InfraredRFReceiveEvent``, which a device
  emits for every burst its ``radio_frequency`` receiver hears and
  aioesphomeapi exposes as a subscription; reached through the esphome
  integration's ``runtime_data``. The same event carries infrared
  receptions, which are set aside.

Timings are signed microseconds, positive for a mark and negative for a space,
and handset following and remote learning see nothing but that list.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, ClassVar

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import SIGNAL_RX_FRAME

if TYPE_CHECKING:
    from aioesphomeapi import InfraredRFReceiveEvent
    from homeassistant.components.esphome.entry_data import RuntimeEntryData

_LOGGER = logging.getLogger(__name__)

type FrameHandler = Callable[[list[int]], None]


class FrameSource(ABC):
    """The receive side of one radio, as seen through its own integration."""

    #: The config entry domain whose radios this implementation listens through.
    domain: ClassVar[str]

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Bind to the config entry that owns the radio."""
        self.hass = hass
        self.entry = entry

    @property
    def id(self) -> str:
        """The persisted name of this source, ``"<domain>:<config entry id>"``."""
        return f"{self.domain}:{self.entry.entry_id}"

    @property
    def name(self) -> str:
        """What to call this source in a picker."""
        return self.entry.title

    @abstractmethod
    @callback
    def async_subscribe(self, handler: FrameHandler) -> CALLBACK_TYPE:
        """Hand every frame the radio hears to ``handler``; returns the unsubscribe."""


class HackrfProxySource(FrameSource):
    """Frames from hass-hackrf-proxy, re-broadcast on a dispatcher signal."""

    domain = "hackrf_proxy"

    @callback
    def async_subscribe(self, handler: FrameHandler) -> CALLBACK_TYPE:
        """Connect to the signal keyed by the proxy's config entry."""

        @callback
        def on_frame(frame: dict[str, Any]) -> None:
            handler(list(frame.get("timings", [])))

        return async_dispatcher_connect(
            self.hass, SIGNAL_RX_FRAME.format(self.entry.entry_id), on_frame
        )


class EsphomeSource(FrameSource):
    """Frames from an ESPHome device, off the native API's receive event.

    Nothing orders this integration after esphome, so the device's entry may
    not be loaded when the subscription is asked for, and it is unloaded and
    set up again whenever the user reloads it. The subscription therefore
    follows the entry's state: attached to the entry's runtime data when it
    is loaded, let go when it stops being loaded. Within a loaded entry, a
    native API subscription lives on one connection and dies with it, so it
    is made again on every reconnect.
    """

    domain = "esphome"

    @callback
    def async_subscribe(self, handler: FrameHandler) -> CALLBACK_TYPE:
        """Subscribe to receive events for as long as the device entry lives."""
        # Deferred so the integration imports on installs without ESPHome.
        from aioesphomeapi import InfraredInfo  # noqa: PLC0415

        entry = self.entry
        unsub_update: CALLBACK_TYPE | None = None
        unsub_receive: CALLBACK_TYPE | None = None

        @callback
        def on_event(event: InfraredRFReceiveEvent) -> None:
            # One event carries both infrared and RF receptions. Home
            # Assistant keeps no record of RF receivers (its esphome platform
            # files only the transmitters), so RF is recognised as "not one
            # of the infrared entities", which it does keep. Whatever else
            # slips through is not a Proflame frame and fails to decode.
            entry_data: RuntimeEntryData = entry.runtime_data
            if (event.device_id, event.key) in entry_data.info.get(InfraredInfo, {}):
                return
            _LOGGER.debug("%s heard %d timings", entry.title, len(event.timings))
            handler(list(event.timings))

        @callback
        def on_device_update() -> None:
            nonlocal unsub_receive
            entry_data: RuntimeEntryData = entry.runtime_data
            if not entry_data.available:
                unsub_receive = None
            elif unsub_receive is None:
                unsub_receive = entry_data.client.subscribe_infrared_rf_receive(on_event)

        @callback
        def attach() -> None:
            nonlocal unsub_update
            if unsub_update is not None:
                return
            entry_data: RuntimeEntryData = entry.runtime_data
            unsub_update = entry_data.async_subscribe_device_updated(on_device_update)
            on_device_update()

        @callback
        def detach() -> None:
            nonlocal unsub_update, unsub_receive
            if unsub_update is not None:
                unsub_update()
                unsub_update = None
            if unsub_receive is not None:
                unsub_receive()
                unsub_receive = None

        @callback
        def on_state_change() -> None:
            if entry.state is ConfigEntryState.LOADED:
                attach()
            else:
                detach()

        if entry.state is ConfigEntryState.LOADED:
            attach()
        else:
            _LOGGER.debug("%s is not loaded; listening once it is", entry.title)
        unsub_state = entry.async_on_state_change(on_state_change)

        @callback
        def unsubscribe() -> None:
            unsub_state()
            detach()

        return unsubscribe


_IMPLEMENTATIONS: dict[str, type[FrameSource]] = {
    HackrfProxySource.domain: HackrfProxySource,
    EsphomeSource.domain: EsphomeSource,
}


@callback
def async_list_sources(hass: HomeAssistant) -> list[FrameSource]:
    """Every source there is, for the picker."""
    return [
        _IMPLEMENTATIONS[entry.domain](hass, entry)
        for entry in hass.config_entries.async_entries()
        if entry.domain in _IMPLEMENTATIONS
    ]


@callback
def async_get_source(hass: HomeAssistant, source_id: str) -> FrameSource | None:
    """The source a persisted id names, or None if its radio is gone."""
    domain, _, entry_id = source_id.partition(":")
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != domain:
        return None
    return _IMPLEMENTATIONS[domain](hass, entry)


@callback
def async_source_for_entry(hass: HomeAssistant, entry_id: str) -> FrameSource | None:
    """The source a config entry offers, if its integration receives at all.

    The radio that transmits usually hears too, so the transmitter's own entry
    is the receiver to try first.
    """
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain not in _IMPLEMENTATIONS:
        return None
    return _IMPLEMENTATIONS[entry.domain](hass, entry)
