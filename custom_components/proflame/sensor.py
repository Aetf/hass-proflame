"""Diagnostics for the one thing here that can fail silently: transmitting.

A command that does not reach the appliance leaves no trace anywhere a person
looks. The believed state is unchanged, so every entity still reads the way it
did a moment ago, and a fireplace nobody has touched for an hour looks exactly
like a radio that has been refusing everything for an hour. These make the
difference visible, and between them show the reconciler's clock — which reads
from whichever of the two is later, since it counts from the last attempt.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, override

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ProflameConfigEntry
from .device import ProflameDevice
from .entity import ProflameEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ProflameConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the transmission diagnostics."""
    device = entry.runtime_data
    async_add_entities(
        [
            ProflameLastSuccess(device),
            ProflameLastFailure(device),
            ProflameFailureCount(device),
        ]
    )


class ProflameDiagnosticSensor(ProflameEntity, SensorEntity):
    """Base for the transmission diagnostics."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    @override
    def available(self) -> bool:
        """Readable even with the radio gone.

        The rest of the entities follow the transmitter, which is right: they
        control an appliance that cannot be reached. These describe the failure
        itself, so going unavailable alongside it would hide the one thing
        worth reading at that moment.
        """
        return True


class ProflameLastSuccess(ProflameDiagnosticSensor):
    """When the appliance was last successfully told anything."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, device: ProflameDevice) -> None:
        """Initialize the sensor."""
        super().__init__(device, "last_transmission")

    @property
    @override
    def native_value(self) -> datetime | None:
        """When the appliance was last successfully told anything.

        The reconciler's clock while things are working, but not by itself: it
        counts from the last *attempt*, so once a transmission has failed the
        deadline runs from that failure instead. Read the later of the two.

        Unknown until something has been sent in this run of Home Assistant,
        which is not a fault: a restart says nothing to the appliance, and that
        gap is precisely what the reconciler closes.
        """
        return self.device.last_success


class ProflameLastFailure(ProflameDiagnosticSensor):
    """When transmitting last failed, and what it said."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, device: ProflameDevice) -> None:
        """Initialize the sensor."""
        super().__init__(device, "last_failure")

    @property
    @override
    def native_value(self) -> datetime | None:
        """When the radio last refused a command."""
        return self.device.last_failure

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any]:
        """The reason, which is the part worth reading.

        An attribute rather than the state: the state is a timestamp so that
        history and templates can do arithmetic on it, and an error string is
        also easily longer than a state value is allowed to be.
        """
        return {"error": self.device.last_error}


class ProflameFailureCount(ProflameDiagnosticSensor):
    """How many transmissions have failed since Home Assistant started."""

    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(self, device: ProflameDevice) -> None:
        """Initialize the sensor."""
        super().__init__(device, "failed_transmissions")

    @property
    @override
    def native_value(self) -> int:
        """The running count.

        Counted rather than merely logged because the interesting failure here
        is the intermittent one — a radio that takes four commands out of five
        works well enough to look healthy and badly enough to lose an off.
        """
        return self.device.failures
