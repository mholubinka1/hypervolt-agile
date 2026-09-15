# hypervolt-agile

Charges your EV during the cheapest Octopus Agile windows by pushing a live schedule to your Hypervolt charger.

---

## How It Works

On each poll cycle the scheduler:

1. Fetches the latest half-hourly Octopus Agile prices via the Octopus API.
2. Selects the cheapest contiguous windows summing to your configured charge duration, filtered by your price limit.
3. Pushes the schedule to your Hypervolt charger over WebSocket.
4. Locks the charger outside scheduled windows, unlocks it during an active window.
5. Holds back re-locking if you cancel a charge via the Hypervolt app, until you re-plug.

Prices and schedules are held in UTC and converted to the charger's local timezone (derived from your Octopus account postcode) at push time. The charger executes the schedule autonomously — the app doesn't need to stay running during a session.

---

## Requirements

- A [Hypervolt](https://hypervolt.co.uk/) v3 home EV charger.
- An [Octopus Energy](https://octopus.energy/) account on the **Agile** tariff.
- Docker (deployment) or Python 3.13+ with [uv](https://docs.astral.sh/uv/) (local development).

---

## Configuration

Copy `config/config.yml.template` to `config/config.yml` and fill in your credentials:

```yaml
octopus:
  account_number: A-XXXXXXXX
  api_key: sk_live_xxxxxxxxxxxxxxxxxxxx
hypervolt:
  username: your@email.com
  password: yourpassword
schedule:
  poll_every_secs: 10 # scheduler run interval, in seconds (2-3600)
  update_every_mins: 30 # Agile price refetch interval, in minutes (1-1440)
  total_charge_duration: 3 # target charge duration, in hours (0-24)
  price_limit_incl_vat: 30 # ultimate ceiling, in p/kWh inc. VAT (1-100; 0 defers fully to a threshold extension, see Extensions)
# log_file: /logs/hypervolt-agile-scheduler.log # omit to log to console only
# log_level: INFO
```

Timezone is derived automatically from your Octopus account postcode — no separate timezone config.

`price_limit_incl_vat` is always an ultimate ceiling: charging never happens above it, regardless of any extension. A threshold extension (`extensions.threshold`) can only lower the effective limit further, never raise it. `price_limit_incl_vat: 0` (valid only when `extensions.threshold` is configured) defers fully to the extension's computed value.

### LED Themes

The charger's LEDs can show one of three built-in seasonal effects. Each is opt-in — listing an effect in `built_in_themes` enables it:

```yaml
led:
  enabled: true
  built_in_themes:
    - effect: halloween_mode
      start: "10-31" # MM-DD, or "MM-DD HH:MM" for a specific time (default 00:00)
      end: "11-01 06:00"
    - effect: christmas_mode
      start: "12-24"
      end: "12-31 06:00"
    - effect: party_mode
      start: "12-31 06:00"
      end: "01-01 06:00"
      always_on: true # light the charger for the whole window regardless of charge/plug state (default false: charging-gated)
```

A displaying theme is always full brightness; otherwise the LEDs are off — there's no dimmed state. See `config/config.yml.template` for the full `led:` block, including `custom_themes` (static colour patterns) and `extensions` (dynamically resolved themes).

Custom-theme colour maps live at `themes/<effect>.yaml`; a `custom_themes` entry naming `effect: <name>` loads it. Preview a map, and edit the LED positions it's painted against, via `themes/reference/charger_led_map.html`.

---

## Extensions

Extensions are Python modules registered in `config.yml` for pluggable behaviour beyond the built-in set. Two kinds ship as working references:

| Kind | Config key | Docs |
| --- | --- | --- |
| LED Theme Extension | `led.extensions` | [extensions/saints_fc.md](extensions/saints_fc.md) |
| Behaviour Provider | `extensions.threshold` | [extensions/behaviours/dynamic_charging_threshold.md](extensions/behaviours/dynamic_charging_threshold.md) |

Extension code loads from a directory separate from `config.yml`, passed via `--extensions-dir` (bind-mounted to `/extensions` in Docker — see [Deployment](#deployment)): extensions are executable code, not declarative data. Leave the directory empty if unused.

Layout differs by kind:

- **LED Theme Extensions** sit flat: `extensions/<name>.py`.
- **Behaviour Providers** sit under a subfolder named for their kind: `extensions/behaviours/<name>.py`. Preserve the subfolder — the `name:` you register (e.g. `behaviours/dynamic_charging_threshold`) is a path relative to the extensions directory.

Each shipped extension's own README (linked above) covers its config and behaviour in full. Add a matching `<name>.md` for any extension you write yourself.

---

## Deployment

### Docker

Examples assume host directories `config`, `log`, `extensions` next to `docker-compose.yml`. Adjust the left side of each `volumes:` entry for a different location.

```bash
mkdir -p config log extensions
```

Place `config.yml` in `config/`, then run:

```bash
docker-compose up -d
```

Custom LED colour maps (`led.custom_themes`) are **baked into the image** from `themes/`. Adding your own means forking, adding `themes/<name>.yaml`, and rebuilding.

> **Upgrading from a version that read `led_effects/`:** move any custom-theme YAMLs into `themes/` and rebuild. No fallback — an unmatched `custom_themes` entry is logged and skipped.

For LED theme extensions or Behaviour Providers, see [Extensions](#extensions).

The container pulls `mholubinka1/hypervolt-agile:latest` from Docker Hub, restarts on failure, and writes rotating logs to the directory mounted at `/logs`.

The published image is ARM64-only (Raspberry Pi–class hosts). Build from `Dockerfile` for another architecture.

### Local Development

```bash
uv sync --frozen
uv run python app/main.py --config-file config/config.yml
```

---

## Development

### Pre-commit Hooks

```bash
pip install pre-commit
pre-commit install
pre-commit run --all-files # manual run
```

| Tool | Purpose |
| --- | --- |
| [black](https://github.com/psf/black) | Code formatting |
| [isort](https://pycqa.github.io/isort/) | Import ordering |
| [mypy](https://mypy-lang.org/) | Type checking |
| [ruff](https://docs.astral.sh/ruff/) | Linting |
| [bandit](https://bandit.readthedocs.io/) | Security scanning |

### CI/CD

Every push and pull request builds a Docker image on a self-hosted ARM64 runner:

- `feature/*` branches → `:dev` tag.
- `main` → `:latest` tag.

[Watchtower](https://containrrr.dev/watchtower/) picks up `:latest` and redeploys automatically.

---

## References

- [home-assistant-hypervolt-charger](https://github.com/gndean/home-assistant-hypervolt-charger) — Hypervolt WebSocket protocol reference
- [Octopus Energy API Guide](https://www.guylipman.com/octopus/api_guide.html)
