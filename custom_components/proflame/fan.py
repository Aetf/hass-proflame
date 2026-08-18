"""The blower."""

from __future__ import annotations

import math
from typing import Any, override

from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util.percentage import (
    percentage_to_ranged_value,
    ranged_value_to_percentage,
)

from . import ProflameConfigEntry
from .entity import ProflameEntity
from .protocol import MAX_LEVEL

#: The blower's own scale. Level 0 is off, so the speed range starts at 1.
SPEED_RANGE = (1, MAX_LEVEL)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ProflameConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the blower."""
    async_add_entities([ProflameBlower(entry.runtime_data)])


class ProflameBlower(ProflameEntity, FanEntity):
    """The convection blower, off or one of six speeds."""

    _attr_supported_features = (
        FanEntityFeature.SET_SPEED | FanEntityFeature.TURN_ON | FanEntityFeature.TURN_OFF
    )
    _attr_speed_count = MAX_LEVEL

    def __init__(self, device: Any) -> None:
        """Initialize the fan."""
        super().__init__(device, "blower")

    @property
    @override
    def is_on(self) -> bool:
        """Whether the blower is believed to be running."""
        return self.device.state.fan > 0

    @property
    @override
    def percentage(self) -> int:
        """The blower speed as a percentage of its six steps."""
        return ranged_value_to_percentage(SPEED_RANGE, self.device.state.fan)

    @override
    async def async_set_percentage(self, percentage: int) -> None:
        """Set the blower speed, where zero stops it."""
        if percentage == 0:
            await self.device.async_set(fan=0)
            return
        level = math.ceil(percentage_to_ranged_value(SPEED_RANGE, percentage))
        await self.device.async_set(fan=max(1, min(MAX_LEVEL, level)))

    @override
    async def async_turn_on(
        self,
        percentage: int | None = None,
        preset_mode: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Start the blower, at full speed unless told otherwise."""
        if percentage is not None:
            await self.async_set_percentage(percentage)
            return
        await self.device.async_set(fan=self.device.state.fan or MAX_LEVEL)

    @override
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Stop the blower."""
        await self.device.async_set(fan=0)
