# `dynamic_charging_threshold` — Behaviour Provider

PHEV-only. Computes a live price/kWh charging threshold from local fuel prices and your car's efficiency, via the [GOV.UK Fuel Finder API](https://www.fuel-finder.service.gov.uk). See [Extensions](../../README.md#extensions) for how extensions load in general.

## Behaviour

1. Geocodes your postcode via [postcodes.io](https://postcodes.io).
2. Pages through the full GOV.UK Fuel Finder dataset (no server-side location filter), ranks stations by distance, and averages your fuel type's price across the nearest `station_count` matches (optionally capped by `radius_miles`).
3. Converts that average to a breakeven price per kWh, using `mpg` and `mi_per_kwh`.
4. Applies a fixed 20% safety margin below breakeven.
5. Caches the result; re-polls on the `schedule.update_every_mins` cadence.

### Interaction with `price_limit_incl_vat`

`price_limit_incl_vat` is always an ultimate ceiling: effective limit = `min(dynamic, static)`.

- **Non-zero** (default): whichever of static/dynamic is lower wins. No fresh value this cycle → static value alone is used.
- **`0`**: defers fully to this extension. No fresh value this cycle → last cached value is reused; none cached yet → charging pauses for that cycle.

## Setup

### 1. Register for API credentials

Register at <https://www.fuel-finder.service.gov.uk> for your own `client_id`/`client_secret` — per-operator, not shared.

### 2. Copy the extension file

Copy `extensions/behaviours/dynamic_charging_threshold.py` into your extensions directory, **preserving the `behaviours/` subfolder** — the `name:` below is a path relative to the extensions directory.

### 3. Configure it

```yaml
schedule:
  price_limit_incl_vat: 0 # 0 = defer fully to this extension; a positive value keeps it as an ultimate ceiling
extensions:
  threshold:
    name: behaviours/dynamic_charging_threshold
    config:
      fuel_type: petrol # required — petrol or diesel (standard grade: E10 / B7_STANDARD)
      mpg: 45.4609 # required — real-world miles-per-gallon on fuel alone
      mi_per_kwh: 3.5 # optional, default 3.5 — real-world miles-per-kWh on electric alone
      postcode: SW1A 1AA # required — a GB postcode
      station_count: 5 # required — number of nearest matching stations to average over
      radius_miles: 10 # optional, default none — excludes stations further than this; does not relax station_count
      client_id: your-fuel-finder-client-id # required
      client_secret: your-fuel-finder-client-secret # required — keep secret
```

`update_every_mins` isn't set here — it's injected automatically from `schedule.update_every_mins`.

### 4. Restart

Restart the app/container — extension files aren't hot-reloaded (a `config.yml` edit alone does trigger an automatic restart).

## Failure handling

Every case below leaves `get_threshold()` returning `None` for that cycle and logs a warning; none crash the app.

| Cause | Result |
| --- | --- |
| Postcode unrecognised / postcodes.io unreachable | No threshold this cycle. |
| Fuel Finder auth fails | No threshold this cycle. |
| Cached token rejected (401) | Auto re-authenticates and retries once; only logged if that also fails. |
| No matching station in range | Logged, naming postcode and fuel type — widen `station_count`/`radius_miles`. |
| Malformed/negative/infinite price | Treated as no data. |
| Threshold computation overflows/underflows | Treated as no data. |
| Any other unexpected error | Logged; previously cached threshold left untouched. |

## Verifying it's working

```text
Dynamic charging threshold extension computed threshold 38.98p/kWh incl VAT from fuel price 171.50p/litre.
```

Only logged when the displayed threshold changes from the previous poll — a flat fuel price across many polling cycles is expected to go quiet, not a sign the extension has stopped. It logs again as soon as the price moves, and also the first time a threshold is recomputed after a cycle with no cached value (e.g. recovering from one of the Failure handling cases above).

The scheduler's own rebuild log line names the winning source (`static`, `dynamic`, `static cap`, `cached dynamic`).

## Troubleshooting

| Symptom | Cause |
| --- | --- |
| `price_limit_incl_vat: 0` rejected at load | `extensions.threshold` isn't configured or fails its own validation. |
| Charging never happens at `price_limit_incl_vat: 0` | No fresh threshold ever produced and nothing cached yet — see Failure handling. |
| "No station... reports fuel type" warning | Increase `station_count`/`radius_miles`, or check `postcode`/`fuel_type`. |
| Always `source: static` | Expected when the static ceiling is lower than the dynamic value that cycle — not a bug. |
| Repeated re-authentication warnings | Check `client_id`/`client_secret` and registration status. |
