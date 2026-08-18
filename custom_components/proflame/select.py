"""Pilot ignition mode."""

from __future__ import annotations

from typing import Any, override

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ProflameConfigEntry
from .entity import ProflameEntity

CONTINUOUS = "continuous"
INTERMITTENT = "intermittent"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ProflameConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the pilot mode."""
    async_add_entities([ProflamePilotMode(entry.runtime_data)])


class ProflamePilotMode(ProflameEntity, SelectEntity):
    """Continuous (CPI) or intermittent (IPI) pilot ignition.

    A select rather than a switch, because neither position is "off": both are
    working modes, and a switch would make one of them look like the absence
    of the other. It is a setting about how the appliance behaves between
    uses, not a control, so it sits under the device's configuration section.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = [CONTINUOUS, INTERMITTENT]

    def __init__(self, device: Any) -> None:
        """Initialize the select."""
        super().__init__(device, "pilot_mode")

    @property
    @override
    def current_option(self) -> str:
        """The pilot mode believed current."""
        return CONTINUOUS if self.device.state.pilot else INTERMITTENT

    @override
    async def async_select_option(self, option: str) -> None:
        """Switch the pilot mode."""
        await self.device.async_set(pilot=option == CONTINUOUS)
