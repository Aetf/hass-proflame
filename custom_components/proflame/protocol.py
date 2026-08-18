"""The SIT Proflame 2 protocol, as Home Assistant needs it.

A Python encoder to match `proxyd/src/proflame.rs`, which stays the reference
implementation; `docs/PROTOCOL.md` is the derivation and `tests/` holds the
captures both are checked against.

Only the encoder lives here. Home Assistant needs to build commands, not
demodulate radio — the daemon does that and delivers decoded timings.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import override

from rf_protocols import ModulationType, RadioFrequencyCommand

#: Manchester half-bit. The whole frame is a multiple of this.
SYMBOL_US = 450

#: Sync, then 11 Manchester bits: 8 data, a start-of-frame flag, parity, stop.
BLOCK_SYMBOLS = 26

#: serial1, serial2, version, cmd1, cmd2, checksum1, checksum2.
FRAME_BLOCKS = 7

#: Silence between repeats of a frame, as measured from the remote.
INTER_FRAME_GAP_US = 4150

#: Repetitions proven on air. The remote sends five per state change; the
#: transmission that first ignited the fireplace sent ten.
DEFAULT_REPEATS = 10

#: 315 MHz is the FCC variant. SIT also ships a 433.92 MHz CE variant, so this
#: is regional rather than fixed.
FCC_FREQUENCY = 315_000_000
CE_FREQUENCY = 433_920_000

#: Levels count 0 to 6 across all three adjustable functions.
MAX_LEVEL = 6


def _nibble(value: int) -> int:
    return (value ^ (value << 5)) & 0xFF


def _mix(byte: int) -> int:
    """The linear map shared by both checksum halves.

    Invertible, but not a CRC — a polynomial search over the inherited packet
    table found nothing, and nothing here needs it to be one.
    """
    return (byte & 0xF0) ^ _nibble(byte & 0x0F) ^ _nibble(byte >> 4)


def checksum(cmd: int, key: int) -> int:
    """Checksum for one half, given that half's per-remote constant."""
    return _mix(cmd) ^ key


def derive_key(cmd: int, observed_checksum: int) -> int:
    """Recover a half's constant from any observed (command, checksum) pair.

    One captured frame fixes both constants, which is why the serial-to-key
    mapping never has to be solved. Note that these are *per remote*: a
    published implementation that hardcodes its author's constants will
    compute wrong checksums for anyone else's handset.
    """
    return _mix(cmd) ^ observed_checksum


@dataclass(frozen=True, slots=True)
class Remote:
    """The identity of one handset, and the checksum constants that go with it."""

    serial1: int
    serial2: int
    version: int
    key1: int
    key2: int

    @classmethod
    def from_frame(
        cls,
        *,
        serial1: int,
        serial2: int,
        version: int,
        cmd1: int,
        cmd2: int,
        checksum1: int,
        checksum2: int,
    ) -> Remote:
        """Build a remote from one captured frame."""
        return cls(
            serial1=serial1,
            serial2=serial2,
            version=version,
            key1=derive_key(cmd1, checksum1),
            key2=derive_key(cmd2, checksum2),
        )


@dataclass(frozen=True, slots=True)
class State:
    """Everything one frame says about the appliance.

    Every frame carries the complete state — the protocol has no deltas — so
    this is what gets transmitted, in full, every time.
    """

    power: bool = False
    flame: int = 0
    fan: int = 0
    light: int = 0
    thermostat: bool = False
    aux: bool = False
    front: bool = False
    pilot: bool = False

    def __post_init__(self) -> None:
        """Reject levels the protocol cannot express."""
        for name in ("flame", "fan", "light"):
            level = getattr(self, name)
            if not 0 <= level <= MAX_LEVEL:
                raise ValueError(f"{name} must be 0..{MAX_LEVEL}, got {level}")

    @classmethod
    def from_commands(cls, cmd1: int, cmd2: int) -> State:
        """Unpack the two command bytes."""
        return cls(
            pilot=bool(cmd1 & 0x80),
            light=(cmd1 >> 4) & 0x07,
            thermostat=bool(cmd1 & 0x02),
            power=bool(cmd1 & 0x01),
            front=bool(cmd2 & 0x80),
            fan=(cmd2 >> 4) & 0x07,
            aux=bool(cmd2 & 0x08),
            flame=cmd2 & 0x07,
        )

    def to_commands(self) -> tuple[int, int]:
        """Pack into the two command bytes.

        Bits 3..2 of `cmd1` are left clear: the layout has no use for them and
        they have been zero in every frame ever received.
        """
        cmd1 = (
            (int(self.pilot) << 7)
            | (self.light << 4)
            | (int(self.thermostat) << 1)
            | int(self.power)
        )
        cmd2 = (int(self.front) << 7) | (self.fan << 4) | (int(self.aux) << 3) | self.flame
        return cmd1, cmd2

    def evolve(self, **changes: object) -> State:
        """A copy with some fields changed.

        The usual way to build a command: take the state believed current and
        change one thing, because a frame always carries everything.
        """
        return replace(self, **changes)  # type: ignore[arg-type]


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

    @property
    def blocks(self) -> list[int]:
        """The seven block values, in air order."""
        cmd1, cmd2 = self.state.to_commands()
        return [
            self.remote.serial1,
            self.remote.serial2,
            self.remote.version,
            cmd1,
            cmd2,
            checksum(cmd1, self.remote.key1),
            checksum(cmd2, self.remote.key2),
        ]

    @override
    def get_raw_timings(self) -> list[int]:
        """Encode as signed microseconds, positive for carrier on.

        The result ends on a mark. A frame's true final space cannot be
        distinguished on air from the silence that follows it, so it belongs to
        the inter-frame gap rather than to the frame — which also makes this
        directly comparable with anything demodulated off the air.
        """
        symbols: list[bool] = []
        for index, value in enumerate(self.blocks):
            # Sync: three marks and a space. Three equal symbols in a row
            # cannot occur in Manchester data, so this is a deliberate code
            # violation that cannot be mistaken for payload.
            symbols += [True, True, True, False]

            bits = [bool(value & (0x80 >> bit)) for bit in range(8)]
            bits.append(index == 0)  # start-of-frame flag, first block only
            bits.append(sum(bits) % 2 == 1)  # even parity over the preceding 9
            bits.append(True)  # stop bit
            for bit in bits:
                symbols += [True, False] if bit else [False, True]

        timings: list[int] = []
        for symbol in symbols:
            signed = SYMBOL_US if symbol else -SYMBOL_US
            if timings and (timings[-1] > 0) == symbol:
                timings[-1] += signed
            else:
                timings.append(signed)
        while timings and timings[-1] < 0:
            timings.pop()
        return timings

    @override
    def __repr__(self) -> str:
        """Return a representation naming the state, which is what matters."""
        return f"ProflameCommand({self.state}, repeat={self.repeat_count})"


@dataclass(frozen=True, slots=True)
class DecodedFrame:
    """What one received burst turned out to be."""

    remote: Remote
    state: State


def decode_frame(timings: list[int]) -> DecodedFrame | None:
    """Decode a burst the daemon received, or None if it is not a clean frame.

    Deliberately all-or-nothing. `proxyd/src/proflame.rs` reports partial
    results because protocol analysis wants them; Home Assistant does not, and
    acting on a frame that failed parity would be worse than ignoring it.

    A burst always ends on a mark — a frame's final space has no terminating
    edge on air — so the tail is restored from the known block length rather
    than treated as an error.
    """
    symbols: list[bool] = []
    for value in timings:
        count = max(1, round(abs(value) / SYMBOL_US))
        symbols += [value > 0] * count
    while len(symbols) % BLOCK_SYMBOLS:
        symbols.append(False)

    if len(symbols) // BLOCK_SYMBOLS != FRAME_BLOCKS:
        return None

    blocks: list[int] = []
    for index in range(FRAME_BLOCKS):
        block = symbols[index * BLOCK_SYMBOLS : (index + 1) * BLOCK_SYMBOLS]
        # Sync: three marks and a space, a deliberate Manchester violation.
        if block[:4] != [True, True, True, False]:
            return None

        bits: list[bool] = []
        for pair_start in range(4, BLOCK_SYMBOLS, 2):
            pair = block[pair_start : pair_start + 2]
            if pair == [True, False]:
                bits.append(True)
            elif pair == [False, True]:
                bits.append(False)
            else:
                return None

        if not bits[10]:  # stop bit
            return None
        if sum(bits[:10]) % 2:  # even parity over data, flag and parity
            return None
        if bits[8] != (index == 0):  # start-of-frame flag
            return None

        value = 0
        for bit in bits[:8]:
            value = (value << 1) | int(bit)
        blocks.append(value)

    serial1, serial2, version, cmd1, cmd2, checksum1, checksum2 = blocks
    return DecodedFrame(
        remote=Remote.from_frame(
            serial1=serial1,
            serial2=serial2,
            version=version,
            cmd1=cmd1,
            cmd2=cmd2,
            checksum1=checksum1,
            checksum2=checksum2,
        ),
        state=State.from_commands(cmd1, cmd2),
    )
