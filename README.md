# SIT Proflame Fireplace for Home Assistant

[![CI](https://github.com/Aetf/hass-proflame/actions/workflows/ci.yml/badge.svg)](https://github.com/Aetf/hass-proflame/actions/workflows/ci.yml)
[![HACS](https://img.shields.io/badge/HACS-custom-41BDF5)](https://hacs.xyz/)

Control a gas fireplace driven by the SIT Proflame 2 remote system from Home
Assistant: fire, flame level, blower, accent light, pilot mode, an optional
thermostat regulating from any temperature sensor you have, and an auto-off
timer. Home Assistant follows the physical handset too, so using one never
confuses the other.

It works over **any `radio_frequency` OOK transmitter** — for example
[hass-hackrf-proxy](https://github.com/Aetf/hass-hackrf-proxy) — rather than
one specific radio dongle. The protocol lives in the
[proflame](https://github.com/Aetf/proflame) library, which the manifest
pip-installs.

## Requirements

- Home Assistant **2026.5 or later** (the `radio_frequency` platform).
- A `radio_frequency` transmitter entity that can reach 315 MHz (FCC) or
  433.92 MHz (CE) — whichever variant your appliance uses.
- For following the handset and for setup's listen step: a transmitter whose
  integration also *receives*, such as hass-hackrf-proxy (see
  [Roadmap](#roadmap) for why receiving is non-standard today).

## Installing

Via [HACS](https://hacs.xyz/): add `https://github.com/Aetf/hass-proflame` as
a custom repository (category: integration), install, and restart Home
Assistant. Manual alternative: copy `custom_components/proflame/` into your
config's `custom_components/` and restart.

## Setting up

Add **SIT Proflame Fireplace** from Settings → Devices & services. The flow
asks for a band and a transmitter, then listens for **one button press on
your handset** and learns everything from that frame: the handset's identity
and both of its checksum constants. Nobody types in hex — and, more to the
point, those constants are *per handset*, so hardcoding anyone's would be
wrong for every other remote.

Afterward:

- **Options**: the temperature sensor (adding one creates the thermostat;
  removing it removes the thermostat) and the state re-send interval
  (0 disables).
- **Reconfigure**: the transmitter and the band — separate from options
  because they change how the appliance is *reached*, and the radio may move
  without the handset changing.

## What you get

| Entity | Field |
|--------|-------|
| `switch` (the device's main entity) | fire on/off |
| `number`, 0–6 | flame level |
| `fan`, six speeds | blower |
| `light`, six levels | accent light |
| `select`, configuration category | pilot — continuous (CPI) or intermittent (IPI) |
| `select` | auto-off timer |
| `climate` | thermostat, once a temperature sensor is configured; carries the blower as its fan mode |
| `binary_sensor`, diagnostic | whether anything is driving the appliance by itself |
| `sensor` ×3, diagnostic | when the radio last succeeded, when it last failed and why, and how many transmissions have failed |

## Worth knowing before relying on it

- **Commands are sent, not confirmed.** The appliance answers no questions,
  so Home Assistant holds a belief and periodically re-asserts it (the
  re-send interval). The diagnostic sensors say when the radio last managed
  to speak, and when it last failed.
- **The handset keeps working.** Home Assistant decodes what the receiver
  hears and follows it. The reverse is impossible — the handset has no
  receiver — so after a command from Home Assistant the handset is stale
  until its next press, which will re-assert its own state.
- **The auto-off timer is the only built-in safety feature, deliberately.**
  Presence, schedules and open windows belong in automations, which can see
  the rest of your house; the timer needs nothing but the fire, survives
  restarts, and stops a regulating thermostat before extinguishing.
- The design rationale behind all of this is written down in
  [docs/DESIGN.md](docs/DESIGN.md), and the integration's full state machine
  in [docs/STATE.md](docs/STATE.md).

## Roadmap

- **Receiving is currently non-standard, and will move when upstream does.**
  Home Assistant's `radio_frequency` platform is transmit-only so far; a
  receiver platform is sketched upstream but does not exist yet. Until it
  does, this integration hears the handset through a dispatcher signal that
  [hass-hackrf-proxy](https://github.com/Aetf/hass-hackrf-proxy)
  re-broadcasts (`hackrf_proxy_rx_frame_{entry_id}`, the daemon's payload
  verbatim). That is a private contract between the two integrations, kept
  payload-compatible with the upstream sketch, so both sides migrate with
  little churn when a real receiver platform lands. Consequence today:
  following the handset works only with a transmitter integration that
  offers this signal; transmit-only setups still control the fireplace but
  go deaf to the handset.
- **Upstreaming.** The intended path is the protocol encoder into Home
  Assistant's `rf-protocols` library first, then this integration into core
  once it meets the quality-scale bar.

## Related work

- [HACS-Proflame2](https://github.com/jeffgregx2/HACS-Proflame2) by
  jeffgregx2 (GPL-3.0, actively developed) — the closest neighbor: a Home
  Assistant integration for the same appliances that drives its own radio
  hardware directly (a LilyGO T-Embed CC1101 over Wi-Fi, or a YardStick One
  on USB), with guided learning, saved profiles, and live listening on the
  LilyGO. Choose it if you want a dedicated, purpose-built dongle path. This
  integration bets on Home Assistant's `radio_frequency` platform instead —
  any OOK transmitter entity works, present or future — and keeps the
  protocol in a reusable library.
- [smartfire](https://github.com/johnellinwood/smartfire) (GPL-3.0, 2020) —
  the original public Proflame 2 reverse engineering, credited in detail in
  the [protocol notes](https://github.com/Aetf/proflame/blob/main/docs/PROTOCOL.md).
- [rtl_433](https://github.com/merbanan/rtl_433) carries a receive-only
  Proflame 2 decoder — handy for watching frames with an RTL-SDR,
  independent of Home Assistant entirely.

## License

MIT OR Apache-2.0, at your option.
