"""The fireplace switch."""

from __future__ import annotations

from typing import Any, override

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ProflameConfigEntry
from .entity import ProflameEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ProflameConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the fireplace's switches."""
    device = entry.runtime_data
    async_add_entities([ProflamePower(device)])


class ProflamePower(ProflameEntity, SwitchEntity):
    """The fire itself."""

    _attr_name = None

    def __init__(self, device: Any) -> None:
        """Initialize the switch."""
        super().__init__(device, "fireplace")

    @property
    @override
    def is_on(self) -> bool:
        """Whether the fire is believed to be lit."""
        return self.device.state.power

    @override
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Light the fire, at whatever level it was left at."""
        await self.device.async_set(power=True)

    @override
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Put the fire out.

        The levels ride along unchanged, which is what the handset does too:
        the appliance comes back at the flame, blower and light it had.
        """
        await self.device.async_set(power=False)
