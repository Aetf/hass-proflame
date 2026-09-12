"""Frame sources: the receive side of a radio, behind one interface."""

# pyright: reportUnknownMemberType=false

from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.proflame.const import SIGNAL_RX_FRAME
from custom_components.proflame.receiver import (
    HackrfProxySource,
    async_get_source,
    async_list_sources,
    async_source_for_entry,
)


async def test_hackrf_proxy_frames_come_off_the_dispatcher_signal(
    hass: HomeAssistant,
) -> None:
    entry = MockConfigEntry(domain="hackrf_proxy", title="sdr")
    entry.add_to_hass(hass)
    source = async_source_for_entry(hass, entry.entry_id)
    assert isinstance(source, HackrfProxySource)
    heard: list[list[int]] = []
    unsubscribe = source.async_subscribe(heard.append)
    async_dispatcher_send(
        hass, SIGNAL_RX_FRAME.format(entry.entry_id), {"frequency": 315000000, "timings": [1, -2]}
    )
    assert heard == [[1, -2]]

    unsubscribe()
    async_dispatcher_send(hass, SIGNAL_RX_FRAME.format(entry.entry_id), {"timings": [3]})
    assert heard == [[1, -2]]


async def test_sources_are_named_by_domain_and_entry(hass: HomeAssistant) -> None:
    radio = MockConfigEntry(domain="hackrf_proxy", title="sdr")
    radio.add_to_hass(hass)
    # A transmit-only integration offers no source, so it is not listed.
    MockConfigEntry(domain="broadlink", title="rm4").add_to_hass(hass)

    listed = async_list_sources(hass)
    assert [source.id for source in listed] == [f"hackrf_proxy:{radio.entry_id}"]
    assert listed[0].name == "sdr"
    assert (
        async_source_for_entry(hass, hass.config_entries.async_entries("broadlink")[0].entry_id)
        is None
    )

    found = async_get_source(hass, listed[0].id)
    assert found is not None and found.entry is radio
    # The id is only good for the radio it was written for: a different domain
    # on the same entry, or an entry that is gone, names nothing.
    assert async_get_source(hass, f"esphome:{radio.entry_id}") is None
    assert async_get_source(hass, "hackrf_proxy:gone") is None
