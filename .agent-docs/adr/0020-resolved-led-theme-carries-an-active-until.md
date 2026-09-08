# A resolved LED Theme carries an `active_until`, supplied by whichever source matched

To log a useful "theme activated" line the schedule coordinator needs to know when the
theme is expected to stop — a value only the matching source knows: for a calendar theme
it is the window end, for the Saints FC extension it is `kick-off + 3h`. We added an
optional `active_until: datetime | None` field to `LedTheme` (the resolution result, not a
catalogue entry — default `None` keeps it invisible on `DEFAULT_BUILT_IN_THEMES` and the
`(LedTheme, Window, Window)` tuples). `resolve_theme` stamps it onto the fresh copy it
already builds: from the matched window end for calendar themes (`_resolve_from` now
returns `(theme, end)`), or lifted from the `LedTheme` the provider returned for
extensions. `LedThemeProvider.resolve` implementations may now set `active_until` on their
returned theme; leaving it `None` (as `resolve_fallback` does — a charge-gated fallback has
no firm end) is valid and means "unknown", and the coordinator simply omits the predicted
end from its log line.

## Considered Options

- **A tuple / result object from `resolve_theme`** (`(LedTheme, datetime | None)`) instead
  of a field. Rejected: it still needs a channel for the *extension* to report its end, so
  either the provider contract grows a parallel return type or `LedTheme` grows the field
  anyway — and every `resolve_theme` call site and its equality-based tests churn for no
  gain. The field is the smaller, single change.
- **The coordinator re-deriving the end itself** from the `custom_themes` / `built_in_themes`
  window tuples it already holds. Rejected: it duplicates the year-anchor matching logic in
  `_resolve_from`, and it structurally cannot see an extension's end (the Saints match
  window — the whole motivating case).

## Consequences

`LedTheme` now serves two subtly different roles — catalogue entry and resolution result —
with a field meaningful only in the second. This is contained by `resolve_theme` being the
sole place `active_until` is written for a returned theme, and by the field never being
consulted for display or wire state (only for the coordinator's transition log).
