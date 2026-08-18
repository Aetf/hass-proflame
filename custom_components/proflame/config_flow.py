"""Config flow for the SIT Proflame integration.

The handset's identity and its two checksum constants are learned by listening
for a single button press, rather than asked for. Nobody should have to type in
hex, and the constants are *per handset*: a published implementation that
hardcodes its author's would compute wrong checksums for anyone else's.
"""

from __future__ import annotations

import asyncio
from typing import Any, override

import voluptuous as vol

from homeassistant.components.radio_frequency import ModulationType, async_get_transmitters
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er, selector
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import (
    CONF_FREQUENCY,
    CONF_KEY1,
    CONF_KEY2,
    CONF_SERIAL1,
    CONF_SERIAL2,
    CONF_TRANSMITTER,
    CONF_VERSION,
    DOMAIN,
    LEARN_TIMEOUT,
    SIGNAL_RX_FRAME,
)
from .protocol import CE_FREQUENCY, FCC_FREQUENCY, Remote, decode_frame


class ProflameConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for a Proflame fireplace."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow."""
        self._transmitter: str | None = None
        self._frequency: int = FCC_FREQUENCY

    @override
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick a band and a transmitter that can reach it."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._frequency = int(user_input[CONF_FREQUENCY])
            self._transmitter = user_input[CONF_TRANSMITTER]
            return await self.async_step_learn()

        # 315 MHz is the FCC variant and 433.92 the CE one, so which band the
        # appliance uses is regional rather than fixed.
        try:
            transmitters = async_get_transmitters(
                self.hass, FCC_FREQUENCY, ModulationType.OOK
            ) + async_get_transmitters(self.hass, CE_FREQUENCY, ModulationType.OOK)
        except HomeAssistantError:
            return self.async_abort(reason="no_transmitters")
        if not transmitters:
            return self.async_abort(reason="no_compatible_transmitters")

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_FREQUENCY, default=str(FCC_FREQUENCY)): vol.In(
                        {
                            str(FCC_FREQUENCY): "315 MHz (FCC)",
                            str(CE_FREQUENCY): "433.92 MHz (CE)",
                        }
                    ),
                    vol.Required(CONF_TRANSMITTER): selector.EntitySelector(
                        selector.EntitySelectorConfig(include_entities=transmitters)
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_learn(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Listen for one button press, to learn which handset this is."""
        assert self._transmitter is not None
        registry = er.async_get(self.hass)
        entity_entry = registry.async_get(self._transmitter)
        if entity_entry is None or entity_entry.config_entry_id is None:
            return self.async_abort(reason="transmitter_unusable")

        if user_input is None:
            return self.async_show_form(step_id="learn")

        remote = await self._async_learn_remote(entity_entry.config_entry_id)
        if remote is None:
            return self.async_show_form(step_id="learn", errors={"base": "no_frame"})

        await self.async_set_unique_id(f"{remote.serial1:02x}{remote.serial2:02x}")
        self._abort_if_unique_id_configured()

        return self.async_create_entry(
            title="Fireplace",
            data={
                CONF_TRANSMITTER: entity_entry.id,
                CONF_FREQUENCY: self._frequency,
                CONF_SERIAL1: remote.serial1,
                CONF_SERIAL2: remote.serial2,
                CONF_VERSION: remote.version,
                CONF_KEY1: remote.key1,
                CONF_KEY2: remote.key2,
            },
        )

    async def _async_learn_remote(self, transmitter_entry_id: str) -> Remote | None:
        """Wait for a frame the receiver hears, and take the handset from it."""
        found: asyncio.Future[Remote] = asyncio.get_running_loop().create_future()

        @callback
        def handle_frame(frame: dict) -> None:
            if found.done():
                return
            decoded = decode_frame(frame.get("timings", []))
            if decoded is not None:
                found.set_result(decoded.remote)

        unsubscribe = async_dispatcher_connect(
            self.hass, SIGNAL_RX_FRAME.format(transmitter_entry_id), handle_frame
        )
        try:
            async with asyncio.timeout(LEARN_TIMEOUT):
                return await found
        except TimeoutError:
            return None
        finally:
            unsubscribe()
