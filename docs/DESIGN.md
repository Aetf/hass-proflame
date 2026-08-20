# Design notes

Why the integration behaves the way it does. The state machine itself —
states, events, edges, invariants — is in [STATE.md](STATE.md); the protocol
lives in the [proflame](https://github.com/Aetf/proflame) library.

## It holds the state because something has to

The appliance is stateless. Every frame carries all of it, nothing can be
queried, and the levels that survive a power cycle survive in the handset. So
this integration keeps its own belief, persists it across restarts, and sends
the complete state on every change.

It also follows the handset, by decoding what the receiver hears. That fixes
one direction only: the handset cannot hear Home Assistant, so after a
command from here it is stale, and the next press on it will transmit that
stale state. The protocol offers no fix; the integration's job is to make
that legible rather than surprising.

## It re-sends the state, because nothing else can correct the appliance

Everything else here transmits when something *changes*, which is not enough:
the appliance can drift away from the belief with nothing changing here — a
frame the air carried, but the appliance did not act on, a handset press
while the receiver was down, a mains interruption. None of that is
detectable, so the only repair available is to say the whole state again.

Re-sending is timed from the last transmission rather than on a fixed clock,
so an actively used fireplace never re-asserts and an untouched one does so
exactly as often as the interval (an option; 0 disables) says. A restart
reconciles as soon as the transmitter is usable, since nothing has been said
in that run at all.

Re-asserting is not symmetric, and that is the safety argument. `power = off`
can only ever extinguish, and the error it corrects — a fire burning that
Home Assistant believes is out — is the one worth correcting. `power = on`
can relight a gas appliance somebody put out, if the belief has gone stale.
What bounds it is that a transmitter able to reconcile is also listening, so
a stale belief needs a radio in range of the fireplace but not of the
handset. Residual rather than zero, and [STATE.md](STATE.md) says so rather
than waving it away.

## The thermostat — and where safety belongs

The appliance's own thermostat lives in the handset and reads a sensor inside
it. The `climate` entity regulates from any sensor Home Assistant can read
instead, and leaves the appliance in manual mode. It appears only once a
sensor is chosen in the options — a thermostat with nothing to read is
furniture.

Its control law is deliberately unhurried, for a reason a normal thermostat
does not have: **every adjustment is about a second of air time on a
half-duplex radio that is deaf while it transmits.** So there is a deadband,
a floor between commands, and a proportional map from deficit to flame level
rather than banging between full and off.

Asking for off and yielding the flame are different events. Setting the mode
to off puts the fire out: somebody said stop heating. Someone moving the
flame slider is not that request — the thermostat yields, leaving both the
mode and the fire alone:

| Situation | `hvac_mode` | `hvac_action` | Driving |
|---|---|---|---|
| regulating, fire lit | `heat` | `heating` | yes |
| regulating, room warm enough | `heat` | `idle` | yes |
| someone moved the flame | `heat` | `off` | no |
| someone switched the fire | `off` | `off` | no |
| asked to stop | `off` | `off` | no |

Switching the fire itself withdraws the request entirely — otherwise the
thermostat would notice a cold room a minute later and light the fire again,
quietly undoing somebody who had just switched it off by hand.

The mode, the target and the yield all survive a restart.

**The auto-off timer is the only safety feature here, and that is on
purpose.** Whether the fire should be lit with nobody home, at night, or with
a window open depends on things this integration has no business knowing;
those belong in an automation, which can see the rest of the house. A timer
needs nothing but the fire: it stops everything driving the appliance before
extinguishing (so a regulating thermostat cannot relight behind it), disarms
only when the fireplace is *finished with* rather than merely idle, and its
deadline is persisted across restarts.

There is deliberately no smart-mode switch. Turning the handset's thermostat
on from Home Assistant hands control back to the handset and the sensor
inside it, which is the opposite of what asking Home Assistant to do
something means — so every command clears that bit instead.

## The state machine is written down

Several bugs here were one bug: each decided *who* had caused a change by
comparing values, which cannot tell "I did that" from "somebody else did the
same thing". [STATE.md](STATE.md) enumerates the states, every event and
every edge, and is where a behavior change should start.
