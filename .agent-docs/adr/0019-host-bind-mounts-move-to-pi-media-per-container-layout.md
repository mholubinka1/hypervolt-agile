# Host bind mounts move to a per-container layout under `/mnt/media/pi-media/containers/`

Until now `docker-compose.yml` bind-mounted the scheduler's three host directories from the
Raspberry Pi's SD card under XDG-style paths: `/home/pi/.config/hypervolt-agile` → `/config`,
`/home/pi/.log/hypervolt-agile` → `/logs`, and `/home/pi/.config/hypervolt-agile-extensions` →
`/extensions`. This spread one service's persistent state across two hidden directories on the
Pi's boot media, alongside every other container's state, with no single place to find or back
up "everything this container owns".

**The decision.** The three host paths move onto the external media drive under a single
per-container root:

| Container path | New host path |
| --- | --- |
| `/config` | `/mnt/media/pi-media/containers/hypervolt-agile-scheduler/config` |
| `/logs` | `/mnt/media/pi-media/containers/hypervolt-agile-scheduler/log` |
| `/extensions` | `/mnt/media/pi-media/containers/hypervolt-agile-scheduler/extensions` |

The container-internal mount points (`/config`, `/logs`, `/extensions`) are unchanged — only the
host side of each bind moves — so `config.yml` (`log_file: /logs/hypervolt-agile-scheduler.log`)
and the `--extensions-dir` default need no edit. The host log directory is named `log` (not
`logs`) to match the sibling convention. The host leaf name has never matched the container
mount here — the old path mounted `.log/hypervolt-agile` at `/logs` — so `log` → `/logs` is if
anything a closer match, not a regression.

**Why.** Everything a container persists now lives under one predictable path keyed by the
container name, on the media drive that is actually backed up, instead of on the Pi's SD card.
`containers/<container-name>/` is the forward convention for this deployment; the older
`octopus-monitoring` stack still uses `/mnt/media/pi-media/monitoring/config/energy-monitor` and
is not being migrated as part of this change.

## Consequences

- These host paths remain personal-deployment detail: they are host-specific and user-editable,
  exactly as the `octopus-monitoring` spec treats its own. This ADR records the *shape* chosen,
  not a contract other deployments must follow.
- The running container on the Pi is defined in an uncommitted, Windows-only copy of
  `pi-desktop/docker/docker-compose.yml`. Applying this change on the Pi means adding the
  service to the Pi's committed `pi-desktop` compose file, which will diverge from the Windows
  copy until reconciled by hand.
- Historical ADRs, specs, and issue files (e.g. ADR 0012, the LED theme specs,
  `.agent-docs/issues/chore-themes-and-charger-led-map.md`) still reference the old
  `/home/pi/.config/hypervolt-agile/...` paths. They are dated records of the state at the time
  and are left unchanged.
