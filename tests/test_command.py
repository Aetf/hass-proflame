"""What a command puts on the air, beyond the frame itself."""

from custom_components.proflame.protocol import ProflameCommand, Remote, State


def test_command_carries_the_inter_frame_gap() -> None:
    # Transmitters that repeat raw timings back-to-back (ESPHome's
    # remote_transmitter) need the gap inside the timings, or every frame's
    # trailing mark fuses with the next frame's sync.
    command = ProflameCommand(
        Remote(serial1=0x21, serial2=0xDD, version=0x02, key1=0x3A, key2=0x9C),
        State(power=True, flame=3),
        frequency=315_000_000,
    )
    timings = command.get_raw_timings()
    assert timings[-1] == -4150
    assert timings[-2] > 0
