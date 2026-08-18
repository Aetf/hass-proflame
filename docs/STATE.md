# The fireplace as a state machine

Five separate bugs in this integration were the same bug. Each was found by
using it, fixed in the place it showed up, and each fix was reasoned out —
twice wrongly. That is a method failing, not luck running out, so this document
enumerates the states, the events and every edge between them, and the next
change to behaviour should start here rather than in a file.

## The mistake they all share

Every one of them came from **inferring who caused a change by comparing
values**.

- The thermostat decided it had been overruled by comparing the flame against
  the level it last asked for. That cannot tell "I set it to 4" from "somebody
  else set it to 4", and it is blind to any field it does not compare — which
  is exactly how switching the fire off slipped past a check that only looked
  at the flame.
- The auto-off timer decided the fireplace was finished with by looking at
  `power`. A thermostat at temperature switches the fire off constantly, so a
  two-hour timer evaporated the first time the room got warm.
- The timer's expiry turned the fire off, and the thermostat lit it again a
  minute later, because nothing said the request itself had ended.

A value comparison answers *what* changed. Every one of these needed to know
*who* changed it, and reconstructing that from a diff is guesswork that gets
harder as fields are added.

**So the design principle is: classify at the source.** Every change to the
believed state carries its origin, and everything downstream reacts to a
classified event instead of re-deriving intent from a diff.

## What state there is

Three things, deliberately separate. Conflating the first two is what made
"turn the thermostat off" and "yield the flame" the same act, which put the
fire out when nobody asked.

### 1. The believed appliance state

`power`, `flame`, `fan`, `light`, `pilot` — and `thermostat`, `aux`, `front`,
which this integration never sets. Not a state machine: any field may take any
legal value. What constrains it is where changes may come from, below.

It is a *belief*. The appliance is stateless, nothing can be queried, and the
levels that survive a power cycle survive in the handset. See
`docs/PROTOCOL.md`.

### 2. Control ownership

Who, if anyone, is driving the appliance on its own.

| state | meaning |
|-------|---------|
| `IDLE` | Nobody. Manual control only. The thermostat's mode is `off`. |
| `REGULATING` | The thermostat is driving. Mode `heat`, and it holds the flame. |
| `YIELDED` | The thermostat still wants to drive — mode stays `heat` — but somebody else took the flame. |

`YIELDED` is why `hvac_mode` and `hvac_action` are separate: the request stands,
the thermostat is simply not the one acting on it.

### 3. The auto-off timer

| state | meaning |
|-------|---------|
| `DISARMED` | No deadline. |
| `ARMED(t)` | At `t`, everything driving the appliance stops and the fire goes out. |

The deadline is persisted, because a restart that forgot it would leave the
fire burning exactly when the thing meant to stop it is gone.

## Origins

Every change to the belief carries exactly one:

| origin | raised by |
|--------|-----------|
| `user` | Any Home Assistant entity acting for a person: the switch, the flame number, the blower, the light, the pilot select, the thermostat's own controls. |
| `thermostat` | The control loop deciding for itself. |
| `timer` | The auto-off deadline expiring. |
| `handset` | A frame decoded off the air — somebody pressing the physical remote, or its own thermostat regulating. |
| `restore` | Loaded from storage at startup. Believed, never transmitted. |

`user` is deliberately not split per entity. Nothing downstream needs to know
*which* control a person used, only that a person used one — and a distinction
nobody consumes is a distinction that will rot.

## Events

Everything that can happen, in full. The tables that follow are indexed by
these.

**From a person, through Home Assistant**

| | event | effect on the belief |
|---|-------|------|
| U1 | switch on / off | `power` |
| U2 | flame number set | `flame` |
| U3 | blower on / off / speed, from the fan entity or the thermostat's fan mode | `fan` |
| U4 | light on / off / brightness | `light` |
| U5 | pilot select | `pilot` |
| U6 | thermostat mode set to `heat` | — |
| U7 | thermostat mode set to `off` | — |
| U8 | thermostat target temperature set | — |
| U9 | auto-off duration selected | — |
| U10 | auto-off set to none | — |

**From the system**

| | event |
|---|-------|
| S1 | The control loop decides on a new `power` and `flame`. |
| S2 | The auto-off deadline is reached. |
| S3 | A frame arrives from the handset. |
| S4 | Home Assistant starts and the belief is restored. |
| S5 | The transmitter becomes unavailable, or comes back. |
| S6 | A transmission fails. |

## Control ownership: every edge

Blank means no change of state.

| event | from `IDLE` | from `REGULATING` | from `YIELDED` |
|-------|-------------|-------------------|----------------|
| U1 switch power | | → `IDLE`, fire left as the person set it | → `IDLE` |
| U2 set flame | | → `YIELDED`, fire untouched | |
| U3 blower | | | |
| U4 light | | | |
| U5 pilot | | | |
| U6 mode `heat` | → `REGULATING`, regulate now | regulate now | → `REGULATING`, regulate now |
| U7 mode `off` | | → `IDLE`, **and put the fire out** | → `IDLE`, **fire untouched** |
| U8 set target | records it | regulate now | → `REGULATING`, regulate now |
| U9 U10 auto-off | | | |
| S1 own command | | | |
| S2 deadline | fire out | → `IDLE`, fire out | → `IDLE`, fire out |
| S3 handset changed `power` | | → `IDLE` | → `IDLE` |
| S3 handset changed `flame` | | → `YIELDED` | |
| S3 handset changed anything else | | | |
| S4 restore | → mode as stored | | |
| S5 transmitter gone | | stays, stops trying | stays |
| S6 send failed | | stays, belief unchanged | stays |

Three of these deserve their reasons written down.

**U1 and S3-power end the request; U2 and S3-flame only hand over.** Switching
the fire off says the fireplace is finished with; switching it on says it is
wanted whatever the thermostat thinks. Either way the request is withdrawn.
Moving the flame leaves the fire wanted and somebody else choosing the level.
Without the first rule the thermostat notices a cold room a minute later and
relights a fire somebody just switched off by hand.

**U7 puts the fire out from `REGULATING` but not from `YIELDED`.** Turning off a
thermostat that is driving the fire means stop heating. Turning off one that
yielded means stop waiting — it is not holding the flame, and taking out a fire
somebody else set would be an act nobody asked for.

**U3 and U4 change nothing.** The blower and the light are not part of
regulating; the thermostat passes the blower through as a convenience. Only the
flame and the power are contested.

## Auto-off: every edge

| event | from `DISARMED` | from `ARMED(t)` |
|-------|------------------|------------------|
| U9 duration selected | → `ARMED(now + d)` | → `ARMED(now + d)`, restarting the clock |
| U10 none selected | | → `DISARMED` |
| S2 deadline reached | | → `DISARMED`, then stop every driver and put the fire out |
| U1 person switches fire off | | → `DISARMED` |
| U7 thermostat off, taking the fire with it | | → `DISARMED` |
| S3 handset switches fire off | | → `DISARMED` |
| S1 thermostat cycles the fire off | | **stays `ARMED`** |
| U1 person switches fire on | | stays; the clock runs from when it was set |
| S4 restore, deadline in the future | → `ARMED(t)` | |
| S4 restore, deadline already passed | → `DISARMED`, then shut down | |

The one that matters is the difference between the last-but-four and the
fourth: *the fire going out* is not the event. **The fire going out at somebody
else's hand** is. A thermostat at temperature switches it off all day.

With origins carried explicitly this stops being a special case: disarm when
`power` goes false and the origin is not `thermostat`.

## Invariants

1. The belief changes through exactly one function, which always receives an
   origin.
2. The belief is updated only after a transmission succeeds, or when a frame is
   received. A failed transmission changes nothing.
3. Every transmission carries the complete state. The appliance is stateless.
4. Every transmission clears the `thermostat` bit: when Home Assistant drives,
   the handset's own thermostat does not.
5. At most one automatic driver exists at a time. Today that is the thermostat.
6. **A change originating from X never causes X to stand itself down.** This is
   the one every bug violated, and carrying the origin is what makes it hold by
   construction rather than by comparing values carefully enough.

## What this analysis found that is still wrong

Writing the tables surfaced four defects that using the integration had not.
None are fixed yet.

**G1. An expiring timer cannot reach a yielded thermostat.** Shutdown stops
everything registered as driving, and a `YIELDED` thermostat deliberately is
not. Its mode stays `heat`, so the next resume relights a fire the timer had
just put out. Row S2 of the ownership table says `YIELDED → IDLE`; the code
does not do it.

**G2. Turning off a yielded thermostat puts the fire out.** Row U7 above
distinguishes the two cases; the code takes the fire out from both.

**G3. The thermostat's mode does not survive a restart.** It is not a
`RestoreEntity`, so a restart silently ends heating and leaves whatever was lit
burning unmanaged. Row S4 says the mode is restored.

**G4. A transmitter outage is invisible to the control loop.** Every tick tries,
fails, and logs; nothing backs off and the thermostat reports itself as
regulating while reaching nothing. Row S5 says it should stay in state but stop
trying.

And the change that would make the rest of it hold by construction rather than
by care: **G5, carry the origin**, replacing `_commanded_flame` and
`_commanded_power`. Those two exist only to guess at origin from values, and
guessing is what this document exists to end.
