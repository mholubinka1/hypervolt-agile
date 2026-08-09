# hypervolt-agile

A scheduler that reads half-hourly Octopus Agile electricity prices and pushes a live charging schedule to a Hypervolt EV charger, so the car charges during the cheapest windows.

## Language

**Agile prices**:
Half-hourly electricity unit prices published by Octopus Energy for the Agile tariff; the app fetches these as the raw input to schedule building.
_Avoid_: tariff rates, unit rates

**Price** (`Price`):
A single half-hourly price period from Octopus, with a value excluding VAT and a validity window (`valid_from`/`valid_to`).
_Avoid_: rate, tariff slot

**Charging period**:
One half-hourly slot counted toward the configured total charge duration; `ScheduleBuilder` converts a duration in hours into a number of charging periods.
_Avoid_: time slot, block

**Charge session** (`ChargeSession` / `HypervoltSession`):
A contiguous window of one or more merged charging periods, with a start/end time and an average price per kWh, that gets pushed to the charger as part of a schedule.
_Avoid_: window, slot, charge window

**Schedule**:
The ordered set of charge sessions currently pushed to the charger; rebuilt each update cycle from the latest Agile prices.
_Avoid_: plan, timetable

**Poll cycle**:
One iteration of the scheduler's main loop (`poll_every_secs`), which checks charger/session state and re-pushes the schedule if needed; distinct from the less-frequent price update cycle (`update_every_mins`).
_Avoid_: tick, loop iteration

**Session clock offset**:
A small buffer trimmed from each charge session's start/end (`SESSION_CLOCK_OFFSET_MINS`) to account for clock drift between the app and the charger.
_Avoid_: buffer, margin

## Charger State

**Lock status** (`LockStatus`: `unlocked`, `pending_lock`, `locked`):
Whether the charger currently permits charging; the scheduler locks the charger outside scheduled windows and unlocks it during an active session.
_Avoid_: charger status

**Charging mode** (`ChargingMode`: `boost`, `eco`, `super_eco`):
The charger's native power-delivery mode, independent of scheduling.

**Activation mode** (`ActivationMode`: `plug_and_charge`, `schedule`, `octopus`):
The charger's source of charging instructions; this app operates by driving the charger while it's in `octopus`/`schedule`-compatible mode.

**Release state** (`ReleaseState`: `DEFAULT`, `RELEASED`):
Tracks user cancellation — when a user stops a charge via the Hypervolt app, the charger enters `RELEASED` and the scheduler holds back from re-locking until the car is re-plugged.
_Avoid_: cancelled, override

**Car plugged**:
Whether a vehicle is currently connected to the charger; combined with release state to decide whether the scheduler should act.

## Boundaries

**Octopus client**:
Talks to the Octopus Energy API to fetch Agile prices and resolve the account's timezone from postcode.

**Hypervolt client**:
Talks to the Hypervolt charger over REST and WebSocket to read state and push schedules/lock commands.

**Charger-local timezone**:
The timezone the charger operates in, derived from the Octopus account postcode; prices and schedules are held in UTC internally and converted to this timezone only when pushed to the charger or formatted for display.
_Avoid_: local time
