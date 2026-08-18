"""The flame level."""

from __future__ import annotations

from typing import Any, override

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ProflameConfigEntry
from .device import Origin
from .entity import ProflameEntity
from .protocol import MAX_LEVEL


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ProflameConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the flame level."""
    async_add_entities([ProflameFlame(entry.runtime_data)])


class ProflameFlame(ProflameEntity, NumberEntity):
    """Main flame height, 0 to 6.

    A number rather than a percentage: the appliance has exactly seven
    positions and the handset counts them, so showing 0–6 says what will
    actually happen. Level 0 with the fire on is the appliance's own lowest
    setting, not "off" — off is the switch.
    """

    _attr_native_min_value = 0
    _attr_native_max_value = MAX_LEVEL
    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER

    def __init__(self, device: Any) -> None:
        """Initialize the number."""
        super().__init__(device, "flame")

    @property
    @override
    def native_value(self) -> float:
        """The flame level believed current."""
        return float(self.device.state.flame)

    @override
    async def async_set_native_value(self, value: float) -> None:
        """Set the flame level."""
        await self.device.async_set(Origin.USER, flame=int(value))
