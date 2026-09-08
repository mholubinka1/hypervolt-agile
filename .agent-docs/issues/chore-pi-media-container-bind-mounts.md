# Issues: chore/pi-media-container-bind-mounts

## Update declared host bind-mount paths in the repo

**Issue**: #145

**Blocked by**: None

**User stories**: 3, 4, 5

### What to build

Update every declared or documented host bind-mount path so it points at
`/mnt/media/pi-media/containers/hypervolt-agile-scheduler/{config,log,extensions}` instead of
`/home/pi/.config/hypervolt-agile`, `/home/pi/.log/hypervolt-agile`, and
`/home/pi/.config/hypervolt-agile-extensions`:

- `docker-compose.yml` — the three `volumes:` host paths. Container paths (`/config`, `/logs`,
  `/extensions`), image, labels, and restart policy unchanged.
- `README.md` — the "Docker (Raspberry Pi)" section: the three `mkdir -p` lines, the "Place
  your `config.yml` in …" line, the extensions-directory line, and the closing "writes rotating
  log files to …" line. The `led_effects/` upgrade note is reworded to drop its hardcoded host
  path (that directory is deprecated per ADR 0012) rather than repointed.
- `config/config.yml.template` — the two comments referencing the old extensions path and the
  old `/logs` bind-mount source.

ADR 0019 (already written) is committed as part of this slice.

### Acceptance criteria

- [ ] `docker compose -f docker-compose.yml config` succeeds and its rendered output shows the
      three new host paths bound to `/config`, `/logs`, `/extensions` respectively.
- [ ] No file under the repo root — excluding `.agent-docs/adr/`, `.agent-docs/specs/`, and
      `.agent-docs/issues/`, which are dated historical records (and include this branch's own
      issue file, which necessarily names the old paths) — references
      `/home/pi/.config/hypervolt-agile`, `/home/pi/.log/hypervolt-agile`, or
      `/home/pi/.config/hypervolt-agile-extensions`.
- [ ] The README "Docker (Raspberry Pi)" section reads coherently end-to-end with the new
      paths.
- [ ] ADR 0019 is present in `.agent-docs/adr/` and explains the per-container layout, the
      `log` → `/logs` name asymmetry, and the deliberate non-migration of `octopus-monitoring`.
- [ ] `pre-commit` passes on all changed files.

---

## Relocate config on the Pi and redeploy the container

**Issue**: #146

**Blocked by**: #145

**User stories**: 1, 2, 6

### What to build

On the `pi-desktop` host, over SSH:

- Create `/mnt/media/pi-media/containers/hypervolt-agile-scheduler/{config,log,extensions}`.
- Copy the live `config.yml` into the new `config/` and `saints_fc.py` into the new
  `extensions/`. `config.yml` only — the timestamped `config.yml.bak` and the deprecated
  `led_effects/` directory are not copied.
- Reconstruct the `hypervolt-agile-scheduler` service block (from `docker inspect` of the
  running container plus this repo's `docker-compose.yml`) with the new host paths and add it
  to `/home/pi/git/pi-desktop/docker/docker-compose.yml`, preserving `container_name`, `image`,
  the watchtower enable label, `depends_on: watchtower`, and `restart: unless-stopped`. Commit
  locally on the Pi.
- `docker compose up -d hypervolt-agile-scheduler` from that directory (service-scoped).
- Verify health and logging, then remove the three old directories.

### Acceptance criteria

- [ ] `/mnt/media/pi-media/containers/hypervolt-agile-scheduler/{config,log,extensions}` exist;
      `config/` contains `config.yml`, `extensions/` contains `saints_fc.py`; no `led_effects/`
      or `.bak` file was copied.
- [ ] The Pi's `pi-desktop/docker/docker-compose.yml` has a `hypervolt-agile-scheduler` service
      with the three new host paths, the watchtower label, and `restart: unless-stopped`, and
      the change is committed locally on the Pi.
- [ ] `docker compose up -d hypervolt-agile-scheduler` recreates only that container —
      `watchtower`, `hueshift2`, and `plex` are not recreated.
- [ ] Within a few minutes the container reports `healthy` with `RestartCount` at 0 or
      unchanged, and `docker inspect` shows its mounts resolving to the three new host paths.
- [ ] `hypervolt-agile-scheduler.log` is present and growing under
      `/mnt/media/pi-media/containers/hypervolt-agile-scheduler/log/`.
- [ ] `/home/pi/.config/hypervolt-agile`, `/home/pi/.config/hypervolt-agile-extensions`, and
      `/home/pi/.log/hypervolt-agile` are removed, and only after every criterion above passes.

---
