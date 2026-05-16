# hypervolt-agile

Automatically charges your EV during the cheapest Octopus Agile windows by pushing a live schedule to your Hypervolt charger.

---

## How It Works

On each poll cycle the scheduler:

1. Fetches the latest half-hourly Octopus Agile prices via the Octopus API
2. Selects the cheapest contiguous windows that sum to your configured charge duration, filtered by your price limit
3. Pushes the schedule to your Hypervolt charger over WebSocket
4. Locks the charger outside scheduled windows and unlocks it when a window is active
5. Respects user cancellation — if you stop a charge via the Hypervolt app, the scheduler holds back until you re-plug

Prices and schedules are maintained in UTC internally and converted to the charger's local timezone (derived from your Octopus account postcode) at push time. The charger executes the schedule autonomously — the app does not need to be running during a session.

---

## Requirements

- A [Hypervolt](https://hypervolt.co.uk/) v3 home EV charger
- An [Octopus Energy](https://octopus.energy/) account on the **Agile** tariff
- Docker (for deployment) or Python 3.13+ with [Poetry](https://python-poetry.org/) (for local development)

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
  poll_every_secs: 10        # How often the scheduler runs (2–3600)
  update_every_mins: 30      # How often to fetch new Agile prices (1–1440)
  total_charge_duration: 3   # Target charge duration in hours (0–24)
  price_limit_incl_vat: 30   # Max price in p/kWh inc. VAT to charge at (0–100)
# log_file: /logs/hypervolt-agile-scheduler.log
# log_level: INFO
```

Your Octopus account postcode is used to determine the charger's timezone automatically — no timezone configuration is needed.

---

## Deployment

### Docker (Raspberry Pi)

Create the required host directories on your Pi:

```bash
mkdir -p /home/pi/.config/hypervolt-agile
mkdir -p /home/pi/.log/hypervolt-agile
```

Place your `config.yml` in `/home/pi/.config/hypervolt-agile/`, then run:

```bash
docker-compose up -d
```

The container pulls `mholubinka1/hypervolt-agile:latest` from Docker Hub, restarts automatically on failure, and writes rotating log files to `/home/pi/.log/hypervolt-agile/`.

### Local Development

Install dependencies:

```bash
poetry install --with dev
```

Run the app:

```bash
poetry run python app/main.py --config-file config/config.yml
```

---

## Development

### Pre-commit Hooks

Pre-commit hooks run automatically on every commit. To install:

```bash
pip install pre-commit
pre-commit install
```

To run manually:

```bash
pre-commit run --all-files
```

The following tools are configured:

| Tool | Purpose |
|------|---------|
| [black](https://github.com/psf/black) | Code formatting |
| [isort](https://pycqa.github.io/isort/) | Import ordering |
| [mypy](https://mypy-lang.org/) | Type checking |
| [ruff](https://docs.astral.sh/ruff/) | Linting |
| [bandit](https://bandit.readthedocs.io/) | Security scanning |

### CI/CD

Every push and pull request triggers a Docker build on a self-hosted ARM64 runner, publishing to Docker Hub:

- `feature/*` branches → `:dev` tag
- `main` → `:latest` tag

[Watchtower](https://containrrr.dev/watchtower/) picks up the `:latest` tag automatically and redeploys the running container on the Pi.

---

## References

- [home-assistant-hypervolt-charger](https://github.com/gndean/home-assistant-hypervolt-charger) — Hypervolt WebSocket protocol reference
- [Octopus Energy API Guide](https://www.guylipman.com/octopus/api_guide.html)
