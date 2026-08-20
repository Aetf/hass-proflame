"""The config flow, including learning a handset from one real encoded frame."""

# Home Assistant test idioms: flow results are TypedDicts asserted key by key,
# and the flow manager's generics leave async_configure partially unknown.
# pyright: reportTypedDictNotRequiredAccess=false, reportUnknownMemberType=false

from unittest.mock import patch

from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send

from proflame import Remote, State, encode_timings

from custom_components.proflame.const import (
    CONF_FREQUENCY,
    CONF_KEY1,
    CONF_KEY2,
    CONF_SERIAL1,
    CONF_SERIAL2,
    CONF_TRANSMITTER,
    CONF_VERSION,
    DOMAIN,
    SIGNAL_RX_FRAME,
)
from custom_components.proflame.protocol import FCC_FREQUENCY

from pytest_homeassistant_custom_component.common import MockConfigEntry

#: A handset with the same shape as a real one, keys and all.
HANDSET = Remote(serial1=0x21, serial2=0xDD, version=0x02, key1=0x3A, key2=0x9C)


async def test_no_radio_frequency_platform_aborts(hass: HomeAssistant) -> None:
    with patch(
        "custom_components.proflame.config_flow.async_get_transmitters",
        side_effect=HomeAssistantError,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_transmitters"


async def test_no_compatible_transmitter_aborts(hass: HomeAssistant) -> None:
    with patch(
        "custom_components.proflame.config_flow.async_get_transmitters",
        return_value=[],
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_compatible_transmitters"


async def test_one_button_press_teaches_the_flow_the_handset(
    hass: HomeAssistant,
) -> None:
    """The whole point of the learn step: identity and keys from one frame.

    The frame is built by the real encoder and heard through the real
    dispatcher path, so what this pins is the flow's contract with the
    transmitter integration — signal name, payload shape, and the decode.
    """
    transmitter_entry = MockConfigEntry(domain="hackrf_proxy", data={})
    transmitter_entry.add_to_hass(hass)
    registry = er.async_get(hass)
    transmitter = registry.async_get_or_create(
        "radio_frequency",
        "hackrf_proxy",
        "test-radio",
        config_entry=transmitter_entry,
    )

    with patch(
        "custom_components.proflame.config_flow.async_get_transmitters",
        return_value=[transmitter.entity_id],
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "user"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_FREQUENCY: str(FCC_FREQUENCY),
                CONF_TRANSMITTER: transmitter.entity_id,
            },
        )
        assert result["type"] is FlowResultType.SHOW_PROGRESS
        assert result["step_id"] == "learn"

        # The handset presses a button: one frame, straight through the
        # dispatcher signal the transmitter integration re-broadcasts on.
        async_dispatcher_send(
            hass,
            SIGNAL_RX_FRAME.format(transmitter_entry.entry_id),
            {
                "frequency": FCC_FREQUENCY,
                "timings": encode_timings(HANDSET, State(power=True, flame=3)),
            },
        )
        await hass.async_block_till_done()

        # Once the frame has been heard the flow advances by itself: the next
        # poll drives progress_done straight through the finish step.
        result = await hass.config_entries.flow.async_configure(result["flow_id"])

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {
        CONF_TRANSMITTER: transmitter.id,
        CONF_FREQUENCY: FCC_FREQUENCY,
        CONF_SERIAL1: HANDSET.serial1,
        CONF_SERIAL2: HANDSET.serial2,
        CONF_VERSION: HANDSET.version,
        CONF_KEY1: HANDSET.key1,
        CONF_KEY2: HANDSET.key2,
    }
    assert result["result"].unique_id == "21dd"
