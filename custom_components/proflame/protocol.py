"""Adapter between the proflame protocol library and Home Assistant.

The protocol itself lives in the `proflame` package on PyPI (pulled in via
the manifest); this module re-exports what the integration uses and adds the
one Home Assistant-shaped piece — `ProflameCommand`, which wraps a complete
appliance state as a `radio_frequency` platform command any OOK transmitter
can carry.
"""

from __future__ import annotations

from typing import override

from proflame import (
    CE_FREQUENCY,
    DEFAULT_REPEATS,
    FCC_FREQUENCY,
    MAX_LEVEL,
    DecodedFrame,
    Remote,
    State,
    decode_frame,
    encode_timings,
)
from rf_protocols import ModulationType, RadioFrequencyCommand

__all__ = [
    "CE_FREQUENCY",
    "DEFAULT_REPEATS",
    "FCC_FREQUENCY",
    "MAX_LEVEL",
    "DecodedFrame",
    "ProflameCommand",
    "Remote",
    "State",
    "decode_frame",
]


class ProflameCommand(RadioFrequencyCommand):
    """A complete appliance state, ready to transmit."""

    def __init__(
        self,
        remote: Remote,
        state: State,
        *,
        frequency: int = FCC_FREQUENCY,
        repeat_count: int = DEFAULT_REPEATS,
        output_power: float | None = None,
    ) -> None:
        """Initialize the command."""
        super().__init__(
            frequency=frequency,
            modulation=ModulationType.OOK,
            repeat_count=repeat_count,
            output_power=output_power,
        )
        self.remote = remote
        self.state = state

    @override
    def get_raw_timings(self) -> list[int]:
        """Encode as signed microseconds, positive for carrier on."""
        return encode_timings(self.remote, self.state)

    @override
    def __repr__(self) -> str:
        """Return a representation naming the state, which is what matters."""
        return f"ProflameCommand({self.state}, repeat={self.repeat_count})"
