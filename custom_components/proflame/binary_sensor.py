"""Diagnostics: whether anything is driving the fireplace by itself."""

from __future__ import annotations

from typing import Any, override

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ProflameConfigEntry
from .entity import ProflameEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ProflameConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the diagnostic sensors."""
    async_add_entities([ProflameManaged(entry.runtime_data)])


class ProflameManaged(BinarySensorEntity, ProflameEntity):
    """Whether something is currently driving the appliance on its own.

    Diagnostic rather than a control: it reports, it does not decide. It exists
    because "the fire is out" means two different things — the thermostat
    pausing, or nobody using the fireplace at all — and everything that has to
    tell those apart was getting it wrong until the distinction was made
    visible. The auto-off timer keys off exactly this.

    Today the thermostat is the only thing that registers, and only while it is
    genuinely regulating: heating, and not yielded to whoever last moved the
    flame.
    """

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, device: Any) -> None:
        """Initialize the sensor."""
        super().__init__(device, "managed")

    @property
    @override
    def is_on(self) -> bool:
        """Whether anything is driving the appliance right now."""
        return self.device.managed
