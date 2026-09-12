"""Frame sources: the receive side of a radio, behind one interface."""

# pyright: reportUnknownMemberType=false

from collections.abc import Callable, Iterator
from unittest.mock import AsyncMock, MagicMock

import pytest
from aioesphomeapi import (
    InfraredInfo,
    InfraredRFReceiveEvent,
    RadioFrequencyCapability,
    RadioFrequencyInfo,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.proflame.receiver import (
    SIGNAL_RX_FRAME,
    EsphomeSource,
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

    listed = await async_list_sources(hass)
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


RECEIVER_KEY = (0, 0xBEEF)
INFRARED_KEY = (0, 0xCAFE)


class FakeEntryData:
    """The slice of esphome's RuntimeEntryData that the source touches."""

    def __init__(self) -> None:
        self.available = True
        self.client = MagicMock()
        self.client.subscribe_infrared_rf_receive.side_effect = self._subscribe
        # What the device answers when asked for its entities; a transmitter
        # only, until a test gives it a receiver.
        self.client.list_entities_services = AsyncMock(
            return_value=([rf_info(RadioFrequencyCapability.TRANSMITTER)], [])
        )
        # Home Assistant files infrared entities but not RF receivers, which
        # is exactly why the source tells them apart by the former.
        self.info = {
            InfraredInfo: {
                INFRARED_KEY: InfraredInfo.from_dict({"key": INFRARED_KEY[1]}),
            }
        }
        self.on_event: Callable[[InfraredRFReceiveEvent], None] | None = None
        self.device_updated: list[Callable[[], None]] = []
        self.unsubscribed = 0

    def _subscribe(self, on_event: Callable[[InfraredRFReceiveEvent], None]) -> Callable[[], None]:
        self.on_event = on_event

        def unsubscribe() -> None:
            self.unsubscribed += 1
            self.on_event = None

        return unsubscribe

    def async_subscribe_device_updated(self, callback_: Callable[[], None]) -> Callable[[], None]:
        self.device_updated.append(callback_)
        return lambda: self.device_updated.remove(callback_)

    def set_available(self, available: bool) -> None:
        """What the esphome integration does on connect and disconnect."""
        self.available = available
        if not available:
            self.on_event = None
        for notify in list(self.device_updated):
            notify()

    def emit(self, key: tuple[int, int], timings: list[int]) -> None:
        assert self.on_event is not None
        self.on_event(
            InfraredRFReceiveEvent.from_dict(
                {"device_id": key[0], "key": key[1], "timings": timings}
            )
        )


def rf_info(capabilities: int) -> RadioFrequencyInfo:
    return RadioFrequencyInfo.from_dict({"key": RECEIVER_KEY[1], "capabilities": capabilities})


@pytest.fixture
def esphome_entry(hass: HomeAssistant) -> Iterator[MockConfigEntry]:
    """An esphome entry whose state the test drives by hand.

    Never left loaded: the hass fixture's teardown unloads loaded entries
    through the real integration, which is not installed here.
    """
    entry = MockConfigEntry(domain="esphome", title="radio")
    entry.add_to_hass(hass)
    try:
        yield entry
    finally:
        entry.mock_state(hass, ConfigEntryState.NOT_LOADED)


def load(hass: HomeAssistant, entry: MockConfigEntry) -> FakeEntryData:
    """Set the entry up the way esphome would: runtime data, then loaded."""
    data = FakeEntryData()
    entry.runtime_data = data
    entry.mock_state(hass, ConfigEntryState.LOADED)
    return data


def unload(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Take the entry down through the states esphome passes on the way."""
    entry.mock_state(hass, ConfigEntryState.UNLOAD_IN_PROGRESS)
    del entry.runtime_data
    entry.mock_state(hass, ConfigEntryState.NOT_LOADED)


def esphome_source(hass: HomeAssistant, entry: MockConfigEntry) -> EsphomeSource:
    source = async_get_source(hass, f"esphome:{entry.entry_id}")
    assert isinstance(source, EsphomeSource)
    return source


async def test_esphome_frames_skip_the_infrared_receiver(
    hass: HomeAssistant, esphome_entry: MockConfigEntry
) -> None:
    data = load(hass, esphome_entry)
    heard: list[list[int]] = []
    unsubscribe = esphome_source(hass, esphome_entry).async_subscribe(heard.append)

    data.emit(INFRARED_KEY, [1, -2])
    assert heard == []
    # A trailing space is delivered as it came; the decoder tolerates it.
    data.emit(RECEIVER_KEY, [1350, -450, 450, -6000])
    assert heard == [[1350, -450, 450, -6000]]

    unsubscribe()
    assert data.unsubscribed == 1
    assert data.device_updated == []


async def test_esphome_subscription_follows_the_connection(
    hass: HomeAssistant, esphome_entry: MockConfigEntry
) -> None:
    data = load(hass, esphome_entry)
    data.available = False
    heard: list[list[int]] = []
    unsubscribe = esphome_source(hass, esphome_entry).async_subscribe(heard.append)
    assert data.on_event is None

    data.set_available(True)
    data.emit(RECEIVER_KEY, [1350, -450])
    assert heard == [[1350, -450]]

    # The connection dropped: the old subscription is gone with it, and
    # the next reconnect must make a fresh one rather than trust the
    # stale handle.
    data.set_available(False)
    data.set_available(True)
    assert data.client.subscribe_infrared_rf_receive.call_count == 2
    data.emit(RECEIVER_KEY, [1350, -450])
    assert len(heard) == 2
    unsubscribe()


async def test_esphome_entry_is_listened_to_once_it_loads(
    hass: HomeAssistant, esphome_entry: MockConfigEntry
) -> None:
    """Nothing orders this integration after esphome, so its entry may load later."""
    heard: list[list[int]] = []
    unsubscribe = esphome_source(hass, esphome_entry).async_subscribe(heard.append)

    esphome_entry.mock_state(hass, ConfigEntryState.SETUP_IN_PROGRESS)
    data = load(hass, esphome_entry)
    assert data.client.subscribe_infrared_rf_receive.call_count == 1
    data.emit(RECEIVER_KEY, [1350, -450])
    assert heard == [[1350, -450]]

    # A reload of the esphome entry brings fresh runtime data; the old one
    # is let go entirely and the new one picked up.
    unload(hass, esphome_entry)
    assert data.unsubscribed == 1
    assert data.device_updated == []
    reloaded = load(hass, esphome_entry)
    assert reloaded.client.subscribe_infrared_rf_receive.call_count == 1
    reloaded.emit(RECEIVER_KEY, [1, -2])
    assert heard == [[1350, -450], [1, -2]]

    unsubscribe()
    assert reloaded.unsubscribed == 1
    unload(hass, esphome_entry)
    assert load(hass, esphome_entry).client.subscribe_infrared_rf_receive.call_count == 0


async def test_hackrf_proxy_always_receives(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain="hackrf_proxy", title="sdr")
    entry.add_to_hass(hass)
    source = async_source_for_entry(hass, entry.entry_id)
    assert source is not None
    assert await source.async_available()


async def test_esphome_receives_only_if_the_device_says_so(
    hass: HomeAssistant, esphome_entry: MockConfigEntry
) -> None:
    """The device is asked, because Home Assistant's own record files no RF receivers."""
    source = esphome_source(hass, esphome_entry)
    assert not await source.async_available()

    data = load(hass, esphome_entry)
    assert not await source.async_available(), "a transmitter alone does not hear"
    assert await async_list_sources(hass) == []

    data.client.list_entities_services.return_value = (
        [
            InfraredInfo.from_dict({"key": INFRARED_KEY[1]}),
            rf_info(RadioFrequencyCapability.TRANSMITTER | RadioFrequencyCapability.RECEIVER),
        ],
        [],
    )
    assert await source.async_available()
    assert [s.id for s in await async_list_sources(hass)] == [source.id]

    data.available = False
    assert not await source.async_available(), "an offline device cannot be asked"
