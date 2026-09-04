# A shipped LED extension may read a repo theme asset via `hypervolt.led`'s public API

ADR 0006 said each `extensions/*.py` module is "isolated and self-contained": every extension
operates from its own `config:` block alone, with no shared or inherited configuration and
nothing read from outside that block. The Saints FC extension (issue #116) now needs the tuned
club-strip colour map that ships at `themes/saints_fc.yaml` — the same map the custom-theme
loader reads, and the single source of truth for those colours (ADR 0012). A strict reading of
ADR 0006 would force the extension to carry its own copy of the 51-entry colour map inline, or
keep the placeholder red/white alternation it shipped with. Both drift from `themes/` the moment
the map is re-tuned.

**The decision.** `extensions/saints_fc.py` imports `THEMES_DIR` and `load_custom_effect` from
`hypervolt.led` and calls `load_custom_effect(THEMES_DIR / "saints_fc.yaml")` once in its
constructor, storing the parsed LEDs on the instance. A missing or malformed file raises from
`__init__`, so `load_extensions` logs it and the extension is treated as absent (ADR 0007),
exactly as for any other construction failure. ADR 0006's "self-contained" is narrowed to its
real intent: **no shared mutable state, and no cross-extension coupling.** A shipped extension
reading a versioned repo asset through `hypervolt.led`'s documented public API is fine — it
ships in the same repo, is released together, and cannot desynchronise from the app it is built
against. The isolation that still holds is that no extension reads another extension's config or
state, and none mutates shared state.

An earlier draft had `main.py` resolve a themes directory and `load_extensions` inject it into
every extension's `config:` dict. That was dropped as needless indirection: the path is already
a public constant on `hypervolt.led`, and only a shipped extension (not an operator's own) has
any reason to reach for a repo asset.

**The alternative rejected.** Keep the placeholder red/white pattern in the extension, or
duplicate `themes/saints_fc.yaml`'s colour map into the extension's own source. Rejected because
the tuned map in `themes/` is the single source of truth for those colours (ADR 0012), the
custom-theme loader already reads it, and a second copy inside the extension would silently
drift from it on any re-tune — the exact failure mode ADR 0012 exists to prevent.
