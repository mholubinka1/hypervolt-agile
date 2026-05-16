# Hypervolt Agile Scheduler

A UK-only application that fetches Octopus Agile half-hourly electricity prices, selects the cheapest charging windows, and pushes a schedule to a Hypervolt EV charger via WebSocket.

## Language

### Pricing

**Agile Price**:
A half-hourly electricity price period from the Octopus Agile tariff, covering a `valid_from`/`valid_to` UTC window and a price in pence/kWh exc. VAT.
_Avoid_: slot, period, unit rate, price point

**Price Limit**:
The maximum per-kWh price (exc. VAT) above which a period is excluded from schedule consideration.
_Avoid_: cap, ceiling, threshold

### Scheduling

**Charge Session**:
A UTC-aware contiguous time window during which charging should occur. The internal representation — never sent to the charger directly.
_Avoid_: slot, window, block, interval

**Schedule**:
The current ordered list of future **Charge Sessions** maintained by the **Scheduler**. Always in UTC. Pruned each cycle to remove expired sessions.
_Avoid_: plan, timetable, agenda

**Schedule Builder**:
The stateless module that transforms a list of **Agile Prices** into a **Schedule** and an average price. Responsible for filtering by **Price Limit**, merging contiguous periods, and applying the clock offset.
_Avoid_: price engine, pricing module, session factory

**Clock Offset**:
A small time margin (in minutes) trimmed from both ends of each merged **Charge Session** to avoid boundary conflicts on the charger. Applied at build time by the **Schedule Builder**.
_Avoid_: buffer, margin, fudge factor

### Charger Interface

**Hypervolt Session**:
A local-time session in the format the charger expects on the wire: a day-of-week, a start time, and an end time. Derived from a **Charge Session** at apply time by converting UTC to the charger's local timezone.
_Avoid_: charger session, wire session, schedule entry

**Activation Mode**:
How the charger decides when to charge: `plug_and_charge` (charge whenever plugged in), `schedule` (follow our schedule), or `octopus` (Octopus-managed tariff mode).
_Avoid_: charging mode, charge mode (reserved for boost/eco)

**Charging Mode**:
The power delivery strategy for a session: `boost`, `eco`, or `super_eco`.
_Avoid_: activation mode (reserved for plug_and_charge/schedule/octopus)

**Lock Status**:
The physical lock state of the charger connector: `unlocked`, `pending_lock`, or `locked`.
_Avoid_: lock state (acceptable in code context), charger state

**Release State**:
Whether the user has manually cancelled a charge via the Hypervolt app. `DEFAULT` means the scheduler is in control; `RELEASED` means the user has interrupted and the scheduler must not act until the car is unplugged and re-plugged.
_Avoid_: override, user state, interrupt

**Charger State**:
The full set of known charger properties at a point in time: lock status, charging mode, activation mode, release state, car plugged, is charging, LED brightness, current schedule. Updated in real time via WebSocket deltas.
_Avoid_: device state, charger snapshot

**State Delta**:
An incremental update to **Charger State** carrying only the fields that changed. Applied by the state manager; fields not present in a delta are left unchanged.
_Avoid_: patch, diff, update object

**LED Theme**:
A pairing of a named **LED Effect** with a year-agnostic calendar window (start date/time → end date/time). The **Scheduler** resolves the active **LED Theme** each cycle and applies it while `is_charging` is `True`. Built-in presets (Halloween, Christmas, Party) have hardcoded windows; custom themes are defined in `config.yml` and reference a YAML file.
_Avoid_: LED mode, LED preset, LED schedule

**LED Effect**:
The visual LED state sent to the charger. Either a named built-in effect (`halloween_mode`, `christmas_mode`, `party_mode`) sent via `effect_name`, or a custom static array (`steady_array`) built from a YAML file defining a `default_colour` and colour `segments` across 51 LEDs (outer ring 0–38, lightning bolt 39–50).
_Avoid_: LED mode, light effect

## Relationships

- The **Scheduler** calls the **Schedule Builder** with **Agile Prices** to produce a **Schedule**
- A **Schedule** is a list of **Charge Sessions** (UTC); the **Scheduler** converts them to **Hypervolt Sessions** (local time) at apply time
- A **Charge Session** that crosses local midnight is split into two **Hypervolt Sessions** at apply time
- The **Scheduler** prunes expired **Charge Sessions** from the **Schedule** each cycle
- The **Scheduler** will not push or lock/unlock when **Release State** is `RELEASED`
- **State Deltas** flow from the WebSocket protocol layer into **Charger State** via a callback

## Example dialogue

> **Dev:** "When do we convert a **Charge Session** to a **Hypervolt Session**?"
> **Domain expert:** "Only at apply time — the **Schedule** stays in UTC throughout. The conversion happens once, just before we push to the charger, using the charger's local timezone."

> **Dev:** "What happens if the user presses stop on the app mid-session?"
> **Domain expert:** "The charger sets **Release State** to `RELEASED`. The **Scheduler** sees this via a **State Delta** and stops pushing or locking until the car is unplugged. Re-plugging resets **Release State** to `DEFAULT`."

## Flagged ambiguities

- "schedule" is used in two senses in the codebase: the internal **Schedule** (list of UTC **Charge Sessions** in `Scheduler._schedule`) and the charger's own schedule (list of **Hypervolt Sessions** confirmed via WebSocket). These are distinct — the internal **Schedule** is the source of truth; the charger's schedule is a reflection of what was last pushed and is retained by the charger through WebSocket reconnections — `current_schedule` remains valid after any reconnect and does not need to be re-pushed.
- "charging mode" was used loosely to mean both **Activation Mode** and **Charging Mode** — these are distinct enums with distinct meanings and must not be conflated.
