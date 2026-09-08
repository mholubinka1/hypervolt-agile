# Move Host Bind Mounts to a Per-Container Layout Under `pi-media`

## Problem Statement

On the Raspberry Pi deployment, everything the `hypervolt-agile-scheduler` container persists
is spread across three hidden directories on the Pi's SD card:

- `/home/pi/.config/hypervolt-agile` → `/config` (holds `config.yml`)
- `/home/pi/.log/hypervolt-agile` → `/logs`
- `/home/pi/.config/hypervolt-agile-extensions` → `/extensions` (holds `saints_fc.py`)

There is no single place to find, inspect, or back up "everything this one container owns",
the paths sit on the Pi's boot media rather than the external media drive that is actually
backed up, and every other container's state is mixed into the same two `.config` / `.log`
trees. The operator wants a consistent per-container layout on the media drive.

## Solution

Introduce a `containers/<container-name>/` convention on the media drive and move this
service's three host directories under it:

| Container path (unchanged) | Old host path | New host path |
| --- | --- | --- |
| `/config` | `/home/pi/.config/hypervolt-agile` | `/mnt/media/pi-media/containers/hypervolt-agile-scheduler/config` |
| `/logs` | `/home/pi/.log/hypervolt-agile` | `/mnt/media/pi-media/containers/hypervolt-agile-scheduler/log` |
| `/extensions` | `/home/pi/.config/hypervolt-agile-extensions` | `/mnt/media/pi-media/containers/hypervolt-agile-scheduler/extensions` |

Only the host side of each bind mount changes. The container-internal mount points stay
`/config`, `/logs`, `/extensions`, so `config.yml` (`log_file: /logs/hypervolt-agile-scheduler.log`)
and the `--extensions-dir` default need no edit. The host log directory is named `log` to
match the sibling-stack convention; the host leaf has never matched the container mount here
(the old path mounted `.log/hypervolt-agile` at `/logs`), so `log` → `/logs` is if anything a
closer match.

The change has two parts:

1. **Repository (this PR):** update `docker-compose.yml`, the `README.md` deployment section,
   and the `config/config.yml.template` comments to the new host paths, and record the
   decision in ADR 0019.
2. **Pi deployment (operational runbook, not in the PR):** create the new directories, copy
   `config.yml` and `saints_fc.py` across, add the `hypervolt-agile-scheduler` service to the
   Pi's committed `pi-desktop/docker/docker-compose.yml` with the new paths, recreate the
   container with `docker compose up -d hypervolt-agile-scheduler`, verify health and logging,
   then delete the three old directories.

## User Stories

1. As the operator, I want every file the scheduler container persists to live under one
   predictable path keyed by the container name, so that I can find, inspect, and back it up
   in one place.
2. As the operator, I want that path to be on the external media drive rather than the Pi's SD
   card, so that it is covered by the drive's backup and not competing for wear on the boot
   media.
3. As the operator, I want the container-internal paths and `config.yml` to be untouched, so
   that the move is a pure relocation with no behavioural change to the running app.
4. As someone reading `docker-compose.yml` or the README later, I want the documented host
   paths to match what is actually deployed, so that a fresh deployment following the README
   lands in the right place.
5. As someone reading the repo history later, I want an ADR explaining why these paths moved
   and why they differ from the `octopus-monitoring` layout, so that the inconsistency is not
   mistaken for a mistake.
6. As the operator, I want the old SD-card directories removed only after the relocated
   container is confirmed healthy and logging, so that there is a rollback point if the move
   goes wrong.

## Implementation Decisions

- **`docker-compose.yml`** — the three `volumes:` host paths change to
  `/mnt/media/pi-media/containers/hypervolt-agile-scheduler/{config,log,extensions}`. Container
  paths, `image`, `pull_policy`, `labels`, `restart` unchanged. This repo's compose file is a
  standalone/reference copy; it is kept correct but is not what deploys the container.
- **`README.md`** — the "Docker (Raspberry Pi)" section: the three `mkdir -p` lines, the
  "Place your `config.yml` in …" line, the extensions-directory line, and the closing "writes
  rotating log files to …" line all move to the new paths. The `led_effects/` upgrade note is
  reworded to drop its hardcoded host path (that directory is deprecated per ADR 0012) rather
  than repointed.
- **`config/config.yml.template`** — the two comments referencing
  `/home/pi/.config/hypervolt-agile-extensions/` and
  "`/logs` is bind-mounted from `/home/pi/.log/hypervolt-agile`" update to the new host paths.
- **ADR 0019** — records the per-container layout decision, the `log` vs `/logs` asymmetry, the
  deliberate non-migration of `octopus-monitoring`, and the Windows-vs-Pi compose divergence
  risk. Already written.
- **Pi runbook (outside the PR):**
  - `mkdir -p /mnt/media/pi-media/containers/hypervolt-agile-scheduler/{config,log,extensions}`.
  - `cp` the live `config.yml` into the new `config/` and `saints_fc.py` into the new
    `extensions/`. `config.yml` only — the timestamped `config.yml.bak` and the deprecated
    `led_effects/` directory (superseded by the repo's `themes/` per ADR 0012, and not
    referenced by the live `config.yml`) are left behind.
  - Reconstruct the `hypervolt-agile-scheduler` service block (from `docker inspect` of the
    running container plus this repo's compose) and add it to
    `/home/pi/git/pi-desktop/docker/docker-compose.yml` with the new host paths, preserving the
    existing `container_name`, `image`, watchtower label, `depends_on: watchtower`, and
    `restart` policy. Commit locally on the Pi.
  - `docker compose up -d hypervolt-agile-scheduler` from that directory (service-scoped, so
    the sibling `watchtower` / `hueshift2` / `plex` services are not recreated).
  - Verify: container state returns to `healthy`, `RestartCount` does not climb, and
    `hypervolt-agile-scheduler.log` appears and grows under the new `log/` directory.
  - `rm -rf` the three old directories once healthy.

## Testing Decisions

- There is no code change and therefore no unit-test seam — the change is YAML, Markdown, and a
  new ADR. The relevant automated check is that `docker-compose.yml` still parses and resolves
  to the intended config: `docker compose -f docker-compose.yml config` succeeds and its
  rendered output shows the three new host paths bound to `/config`, `/logs`, `/extensions`.
- A docs-consistency check: no committed file under the repo root — excluding `.agent-docs/adr/`,
  `.agent-docs/specs/`, and `.agent-docs/issues/`, which are dated historical records (the last
  of which includes this branch's own issue file, which necessarily names the old paths) — still
  references `/home/pi/.config/hypervolt-agile`, `/home/pi/.log/hypervolt-agile`, or
  `/home/pi/.config/hypervolt-agile-extensions`.
- End-to-end verification is operational, on the Pi, per the runbook above — the container
  coming up `healthy` with logs flowing to the new `log/` directory is the acceptance signal.
  This matches the project's established practice of verifying deployment-shaped changes in the
  execution environment (see `chore-downgrade-websocket-reconnect-log.md`).

## Out of Scope

- Migrating `octopus-monitoring` or any other container to the new layout.
- Changing container-internal mount points (`/config`, `/logs`, `/extensions`) or editing
  `config.yml` on the Pi.
- Moving the deprecated `led_effects/` directory or the timestamped `config.yml.bak`.
- Rewriting historical ADRs, specs, and issue files that reference the old `/home/pi/...` paths
  (e.g. `.agent-docs/issues/chore-themes-and-charger-led-map.md`).
- Reconciling the Pi's committed `pi-desktop/docker/docker-compose.yml` with the uncommitted
  Windows copy that currently owns the running container — the Pi-side commit will diverge
  until the operator merges it by hand on Windows. Flagged in ADR 0019 as a known
  consequence.
- Any change to `watchtower`, `hueshift2`, `plex`, or other services in the Pi's compose file.

## Further Notes

- The Pi's live `config.yml` contains real Octopus and Hypervolt credentials. The runbook only
  `cp`s the file on the Pi; its contents are never copied into the repo, the PR, or any
  generated artifact.
- The running container's compose metadata points at
  `C:\Users\mehol\git\pi-desktop\docker\docker-compose.yml` (project `docker`), a file that
  exists only on the operator's Windows machine and was never committed. The Pi's checkout of
  `pi-desktop` is level with `origin/main` and contains no `hypervolt` service. This is why the
  runbook adds the service to the Pi's compose file rather than editing an existing entry.
