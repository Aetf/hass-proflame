"""The config flow, including learning a handset from one real encoded frame."""

# Home Assistant test idioms: flow results are TypedDicts asserted key by key,
# and the flow manager's generics leave async_configure partially unknown.
# pyright: reportTypedDictNotRequiredAccess=false, reportUnknownMemberType=false

from collections.abc import Callable, Iterator
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from aioesphomeapi import InfraredRFReceiveEvent
from homeassistant.config_entries import SOURCE_RECONFIGURE, SOURCE_USER, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from proflame import Remote, State, encode_timings
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.proflame import async_migrate_entry
from custom_components.proflame.const import (
    CONF_FREQUENCY,
    CONF_KEY1,
    CONF_KEY2,
    CONF_RECEIVER,
    CONF_SERIAL1,
    CONF_SERIAL2,
    CONF_TRANSMITTER,
    CONF_VERSION,
    DOMAIN,
)
from custom_components.proflame.protocol import FCC_FREQUENCY
from custom_components.proflame.receiver import SIGNAL_RX_FRAME

#: A handset with the same shape as a real one, keys and all.
HANDSET = Remote(serial1=0x21, serial2=0xDD, version=0x02, key1=0x3A, key2=0x9C)

#: What the handset transmits when a button is pressed.
FRAME = encode_timings(HANDSET, State(power=True, flame=3))


@pytest.fixture
def transmitter(hass: HomeAssistant) -> er.RegistryEntry:
    """A radio_frequency transmitter owned by a hackrf_proxy entry."""
    transmitter_entry = MockConfigEntry(domain="hackrf_proxy", title="sdr", data={})
    transmitter_entry.add_to_hass(hass)
    return er.async_get(hass).async_get_or_create(
        "radio_frequency",
        "hackrf_proxy",
        "test-radio",
        config_entry=transmitter_entry,
    )


def owner_of(transmitter: er.RegistryEntry) -> str:
    assert transmitter.config_entry_id is not None
    return transmitter.config_entry_id


@pytest.fixture
def esphome_radio(hass: HomeAssistant) -> Iterator[tuple[MockConfigEntry, MagicMock]]:
    """An esphome entry that looks loaded, with a client that records its subscriber.

    Marked loaded only for the test's duration: the hass fixture's teardown
    unloads loaded entries through the real integration, which is not
    installed here.
    """
    entry = MockConfigEntry(domain="esphome", title="node")
    entry.add_to_hass(hass)
    client = MagicMock()
    client.subscribe_infrared_rf_receive.return_value = lambda: None
    entry.runtime_data = MagicMock(available=True, client=client, info={})
    entry.runtime_data.async_subscribe_device_updated.return_value = lambda: None
    entry.mock_state(hass, ConfigEntryState.LOADED)
    try:
        yield entry, client
    finally:
        entry.mock_state(hass, ConfigEntryState.NOT_LOADED)


async def test_no_radio_frequency_platform_aborts(hass: HomeAssistant) -> None:
    with patch(
        "custom_components.proflame.config_flow.async_get_transmitters",
        side_effect=HomeAssistantError,
    ):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_transmitters"


async def test_no_compatible_transmitter_aborts(hass: HomeAssistant) -> None:
    with patch(
        "custom_components.proflame.config_flow.async_get_transmitters",
        return_value=[],
    ):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_compatible_transmitters"


async def test_one_button_press_teaches_the_flow_the_handset(
    hass: HomeAssistant, transmitter: er.RegistryEntry
) -> None:
    """The whole point of the learn step: identity and keys from one frame.

    The frame is built by the real encoder and heard through the real
    dispatcher path, so what this pins is the flow's contract with the
    transmitter integration — signal name, payload shape, and the decode.
    """
    with patch(
        "custom_components.proflame.config_flow.async_get_transmitters",
        return_value=[transmitter.entity_id],
    ):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "user"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_FREQUENCY: str(FCC_FREQUENCY),
                CONF_TRANSMITTER: transmitter.entity_id,
            },
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "receiver"
        # The radio that owns the transmitter is offered first, since it
        # usually hears too.
        own = f"hackrf_proxy:{owner_of(transmitter)}"
        assert result["data_schema"] is not None
        assert result["data_schema"]({}) == {CONF_RECEIVER: own}

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_RECEIVER: own}
        )
        assert result["type"] is FlowResultType.SHOW_PROGRESS
        assert result["step_id"] == "learn"

        # The handset presses a button: one frame, straight through the
        # dispatcher signal the transmitter integration re-broadcasts on.
        async_dispatcher_send(
            hass,
            SIGNAL_RX_FRAME.format(owner_of(transmitter)),
            {"frequency": FCC_FREQUENCY, "timings": FRAME},
        )
        await hass.async_block_till_done()

        # Once the frame has been heard the flow advances by itself: the next
        # poll drives progress_done straight through the finish step.
        result = await hass.config_entries.flow.async_configure(result["flow_id"])

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {
        CONF_TRANSMITTER: transmitter.id,
        CONF_RECEIVER: own,
        CONF_FREQUENCY: FCC_FREQUENCY,
        CONF_SERIAL1: HANDSET.serial1,
        CONF_SERIAL2: HANDSET.serial2,
        CONF_VERSION: HANDSET.version,
        CONF_KEY1: HANDSET.key1,
        CONF_KEY2: HANDSET.key2,
    }
    assert result["result"].unique_id == "21dd"


async def test_the_receiver_need_not_be_the_transmitter(
    hass: HomeAssistant,
    transmitter: er.RegistryEntry,
    esphome_radio: tuple[MockConfigEntry, MagicMock],
) -> None:
    """A HackRF transmits; an ESPHome node by the fireplace hears the handset."""
    esphome_entry, client = esphome_radio
    receiver = f"esphome:{esphome_entry.entry_id}"

    with patch(
        "custom_components.proflame.config_flow.async_get_transmitters",
        return_value=[transmitter.entity_id],
    ):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_FREQUENCY: str(FCC_FREQUENCY), CONF_TRANSMITTER: transmitter.entity_id},
        )
        assert result["step_id"] == "receiver"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_RECEIVER: receiver}
        )
        assert result["type"] is FlowResultType.SHOW_PROGRESS

        on_event: Callable[[InfraredRFReceiveEvent], None] = (
            client.subscribe_infrared_rf_receive.call_args.args[0]
        )
        on_event(InfraredRFReceiveEvent.from_dict({"key": 1, "timings": FRAME}))
        await hass.async_block_till_done()
        result = await hass.config_entries.flow.async_configure(result["flow_id"])

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_TRANSMITTER] == transmitter.id
    assert result["data"][CONF_RECEIVER] == receiver
    assert result["data"][CONF_SERIAL1] == HANDSET.serial1


async def test_no_receiver_at_all_aborts_setup(hass: HomeAssistant) -> None:
    """Learning listens, so setup is impossible without something to listen with."""
    transmitter_entry = MockConfigEntry(domain="broadlink", data={})
    transmitter_entry.add_to_hass(hass)
    transmitter = er.async_get(hass).async_get_or_create(
        "radio_frequency", "broadlink", "rm4", config_entry=transmitter_entry
    )
    with patch(
        "custom_components.proflame.config_flow.async_get_transmitters",
        return_value=[transmitter.entity_id],
    ):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_receivers"


def fireplace(transmitter: er.RegistryEntry, **data: Any) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        minor_version=2,
        unique_id="21dd",
        data={
            CONF_TRANSMITTER: transmitter.id,
            CONF_FREQUENCY: FCC_FREQUENCY,
            CONF_SERIAL1: HANDSET.serial1,
            CONF_SERIAL2: HANDSET.serial2,
            CONF_VERSION: HANDSET.version,
            CONF_KEY1: HANDSET.key1,
            CONF_KEY2: HANDSET.key2,
            **data,
        },
    )


async def test_reconfigure_can_drop_the_receiver(
    hass: HomeAssistant, transmitter: er.RegistryEntry
) -> None:
    """Transmit-only is allowed once the handset is known: it just goes unfollowed."""
    own = f"hackrf_proxy:{owner_of(transmitter)}"
    entry = fireplace(transmitter, **{CONF_RECEIVER: own})
    entry.add_to_hass(hass)

    with (
        patch(
            "custom_components.proflame.config_flow.async_get_transmitters",
            return_value=[transmitter.entity_id],
        ),
        patch("custom_components.proflame.async_setup_entry", return_value=True),
        patch("custom_components.proflame.async_unload_entry", return_value=True),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id},
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "reconfigure"
        # The current receiver is suggested, not forced: the field can be cleared.
        assert result["data_schema"] is not None
        markers = {str(marker): marker for marker in result["data_schema"].schema}
        assert markers[CONF_RECEIVER].description == {"suggested_value": own}

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_FREQUENCY: str(FCC_FREQUENCY), CONF_TRANSMITTER: transmitter.entity_id},
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert CONF_RECEIVER not in entry.data
    assert entry.data[CONF_TRANSMITTER] == transmitter.id


async def test_entries_without_a_receiver_keep_listening_through_the_transmitter(
    hass: HomeAssistant, transmitter: er.RegistryEntry
) -> None:
    entry = fireplace(transmitter)
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(entry, minor_version=1)

    assert await async_migrate_entry(hass, entry)
    assert entry.minor_version == 2
    assert entry.data[CONF_RECEIVER] == f"hackrf_proxy:{owner_of(transmitter)}"
