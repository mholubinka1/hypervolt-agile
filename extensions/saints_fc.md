# `saints_fc` — LED Theme Extension

Lights the charger's LED strip for Southampton FC match days, using live fixture data from [TheSportsDB](https://www.thesportsdb.com/). See [Extensions](../README.md#extensions) for how extensions load in general.

## Behaviour

- Polls TheSportsDB for upcoming and recently completed fixtures for the configured team.
- **Match window**: 30 minutes before kick-off to 3 hours after. Always-on and outranks every other theme.
- **Outside the window on a match day**: charging-gated fallback, below custom and built-in themes.
- **Non-match day**: contributes nothing.
- A fixture with unknown kick-off time has no match window that day — fallback-only until the time firms up.
- Checks today's fixtures once at startup, before the recurring poll begins, so a same-day restart doesn't miss a match.
- A poll failure is logged; the last confirmed fixture data is left untouched.

## Setup

1. Copy `extensions/saints_fc.py` into your extensions directory, flat (no subfolder).
2. Register it under `led.extensions`:

   ```yaml
   led:
     enabled: true
     extensions: # highest priority — checked before custom_themes and built_in_themes
       - name: saints_fc
         config:
           api_key: "3" # optional, default "3" — TheSportsDB's free shared key; swap for a personal key for dedicated rate limits
           team_id: 134778 # optional, default 134778 — a TheSportsDB team ID
           poll_interval_hours: 1 # optional, default 1 — fixture re-check interval, in hours
   ```

3. Restart the app/container — extension files aren't hot-reloaded.

## Requirements

- `themes/saints_fc.yaml` colour map — shipped by default.
- No API registration needed for the default shared key.

## Verifying it's working

```text
LED theme extension 'saints_fc' tracking TheSportsDB team_id 134778.
```

A poll failure logs:

```text
LED theme extension 'saints_fc' poll failed: <ExceptionType>: <message>.
```

## Troubleshooting

| Symptom | Cause |
| --- | --- |
| Strip never lights, no error | Not listed under `led.extensions`, or `led.enabled: false`. |
| Strip lights for the wrong club | `team_id` is set to an incorrect TheSportsDB ID. |
| Repeated `poll failed` warnings | Shared key rate-limited, or network issue reaching `thesportsdb.com`. Try a personal key. |
| No tracking line at startup | Extension failed to load — check for a config-validation error logged just before it. |
