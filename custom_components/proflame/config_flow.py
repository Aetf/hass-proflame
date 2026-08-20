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
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er, selector
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import (
    CONF_FREQUENCY,
    CONF_TEMPERATURE_SENSOR,
    CONF_KEY1,
    CONF_KEY2,
    CONF_RECONCILE_INTERVAL,
    CONF_SERIAL1,
    CONF_SERIAL2,
    CONF_TRANSMITTER,
    CONF_VERSION,
    DEFAULT_RECONCILE_INTERVAL,
    DOMAIN,
    LEARN_TIMEOUT,
    SIGNAL_RX_FRAME,
)
from .protocol import CE_FREQUENCY, FCC_FREQUENCY, Remote, decode_frame


class ProflameConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for a Proflame fireplace."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> ProflameOptionsFlow:
        """Get the options flow."""
        return ProflameOptionsFlow()

    def __init__(self) -> None:
        """Initialize the flow."""
        self._transmitter: str | None = None
        self._frequency: int = FCC_FREQUENCY
        self._learn_task: asyncio.Task[Remote | None] | None = None
        self._remote: Remote | None = None

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
                    vol.Required(CONF_TRANSMITTER): selector.EntitySelector(  # pyright: ignore[reportUnknownMemberType]
                        selector.EntitySelectorConfig(include_entities=transmitters)
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_learn(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Listen for one button press, to learn which handset this is.

        A progress step rather than a form. A form would have to be submitted
        before the listening started, leaving nothing on screen to say whether
        to press the button before or after — and then freezing for a minute
        either way. This says it is listening *while* it listens, and moves on
        by itself the moment a frame arrives.
        """
        assert self._transmitter is not None
        registry = er.async_get(self.hass)
        entity_entry = registry.async_get(self._transmitter)
        if entity_entry is None or entity_entry.config_entry_id is None:
            return self.async_abort(reason="transmitter_unusable")

        if self._learn_task is None:
            self._learn_task = self.hass.async_create_task(
                self._async_learn_remote(entity_entry.config_entry_id)
            )

        if not self._learn_task.done():
            return self.async_show_progress(
                step_id="learn",
                progress_action="listening",
                progress_task=self._learn_task,
            )

        self._remote = self._learn_task.result()
        self._learn_task = None
        return self.async_show_progress_done(
            next_step_id="finish" if self._remote is not None else "retry"
        )

    async def async_step_finish(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Record the handset that was heard."""
        assert self._remote is not None and self._transmitter is not None
        remote = self._remote
        entity_entry = er.async_get(self.hass).async_get(self._transmitter)
        if entity_entry is None:
            return self.async_abort(reason="transmitter_unusable")

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

    async def async_step_retry(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Nothing was heard; offer to listen again."""
        if user_input is not None:
            return await self.async_step_learn()
        return self.async_show_form(step_id="retry")

    async def _async_learn_remote(self, transmitter_entry_id: str) -> Remote | None:
        """Wait for a frame the receiver hears, and take the handset from it."""
        found: asyncio.Future[Remote] = asyncio.get_running_loop().create_future()

        @callback
        def handle_frame(frame: dict[str, Any]) -> None:
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

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Point the fireplace at a different transmitter, or another band.

        Separate from the options flow because it changes how the appliance is
        reached rather than how it behaves — and because the radio is expected
        to move: it is a laptop today and something permanent later. Nothing
        here re-learns the handset, which has not changed.
        """
        entry = self._get_reconfigure_entry()

        if user_input is not None:
            transmitter = er.async_get(self.hass).async_get(user_input[CONF_TRANSMITTER])
            if transmitter is None:
                return self.async_abort(reason="transmitter_unusable")
            return self.async_update_reload_and_abort(
                entry,
                data={
                    **entry.data,
                    CONF_FREQUENCY: int(user_input[CONF_FREQUENCY]),
                    CONF_TRANSMITTER: transmitter.id,
                },
            )

        try:
            transmitters = async_get_transmitters(
                self.hass, FCC_FREQUENCY, ModulationType.OOK
            ) + async_get_transmitters(self.hass, CE_FREQUENCY, ModulationType.OOK)
        except HomeAssistantError:
            return self.async_abort(reason="no_transmitters")

        current = er.async_get(self.hass).async_get(entry.data[CONF_TRANSMITTER])
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_FREQUENCY, default=str(entry.data.get(CONF_FREQUENCY, FCC_FREQUENCY))
                    ): vol.In(
                        {
                            str(FCC_FREQUENCY): "315 MHz (FCC)",
                            str(CE_FREQUENCY): "433.92 MHz (CE)",
                        }
                    ),
                    vol.Required(
                        CONF_TRANSMITTER,
                        default=current.entity_id if current else None,
                    ): selector.EntitySelector(  # pyright: ignore[reportUnknownMemberType]
                        selector.EntitySelectorConfig(include_entities=transmitters)
                    ),
                }
            ),
        )


class ProflameOptionsFlow(OptionsFlow):
    """Settings that change how the fireplace behaves, not how it is reached."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose the temperature source, and how often to re-assert the state.

        Leaving the sensor empty removes the thermostat entirely rather than
        leaving a climate entity that cannot read a temperature.
        """
        if user_input is not None:
            options: dict[str, Any] = {
                CONF_RECONCILE_INTERVAL: int(user_input[CONF_RECONCILE_INTERVAL])
            }
            if sensor := user_input.get(CONF_TEMPERATURE_SENSOR):
                options[CONF_TEMPERATURE_SENSOR] = sensor
            return self.async_create_entry(data=options)

        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(
                    {
                        vol.Optional(CONF_TEMPERATURE_SENSOR): selector.EntitySelector(  # pyright: ignore[reportUnknownMemberType]
                            selector.EntitySelectorConfig(
                                domain="sensor", device_class="temperature"
                            )
                        ),
                        vol.Required(
                            CONF_RECONCILE_INTERVAL,
                            default=DEFAULT_RECONCILE_INTERVAL,
                        ): selector.NumberSelector(  # pyright: ignore[reportUnknownMemberType]
                            selector.NumberSelectorConfig(
                                min=0,
                                max=180,
                                step=5,
                                unit_of_measurement="min",
                                mode=selector.NumberSelectorMode.BOX,
                            )
                        ),
                    }
                ),
                self.config_entry.options,
            ),
        )
