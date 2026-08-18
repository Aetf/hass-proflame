"""The accent light."""

from __future__ import annotations

import math
from typing import Any, override

from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity
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
    """Set up the accent light."""
    async_add_entities([ProflameLight(entry.runtime_data)])


class ProflameLight(ProflameEntity, LightEntity):
    """The accent light, off or one of six levels.

    Home Assistant's brightness is 1–255 and the appliance has six steps, so
    the two are mapped onto each other rather than pretending to a precision
    the hardware does not have.
    """

    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}

    def __init__(self, device: Any) -> None:
        """Initialize the light."""
        super().__init__(device, "accent_light")

    @property
    @override
    def is_on(self) -> bool:
        """Whether the light is believed to be lit."""
        return self.device.state.light > 0

    @property
    @override
    def brightness(self) -> int | None:
        """The light level, scaled to Home Assistant's range."""
        level = self.device.state.light
        if level == 0:
            return None
        return round(level * 255 / MAX_LEVEL)

    @override
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Light it, at full brightness unless told otherwise."""
        if (brightness := kwargs.get(ATTR_BRIGHTNESS)) is not None:
            level = max(1, min(MAX_LEVEL, math.ceil(brightness * MAX_LEVEL / 255)))
        else:
            level = self.device.state.light or MAX_LEVEL
        await self.device.async_set(Origin.USER, light=level)

    @override
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn it off."""
        await self.device.async_set(Origin.USER, light=0)
