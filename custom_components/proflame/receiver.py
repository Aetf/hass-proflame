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

Timings are signed microseconds, positive for a mark and negative for a space,
and handset following and remote learning see nothing but that list.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any, ClassVar

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import SIGNAL_RX_FRAME

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


_IMPLEMENTATIONS: dict[str, type[FrameSource]] = {
    HackrfProxySource.domain: HackrfProxySource,
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
