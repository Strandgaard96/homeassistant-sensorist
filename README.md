# Sensorist for Home Assistant

Custom integration exposing [Sensorist](https://sensorist.com) environment sensors
in Home Assistant, via the Sensorist cloud REST API.

## Install

Sensorist has no local API — the gateway talks only to the cloud — so this is a
cloud-polling integration.

### HACS (custom repository)

1. HACS → **Integrations** → ⋮ → **Custom repositories**
2. Add `https://github.com/magst/homeassistant-sensorist`, category **Integration**
3. Install **Sensorist**, then restart Home Assistant
4. **Settings → Devices & Services → Add Integration → Sensorist**
5. Sign in with the email and password you use for the Sensorist app

### Manual

Copy `custom_components/sensorist` into your Home Assistant `config/custom_components/`
directory and restart.

## What you get

One Home Assistant device per Sensorist **sensor**, linked to a device for the
**gateway** it reports through.

| Entity | Device class | Unit | Notes |
|---|---|---|---|
| Temperature | `temperature` | °C | |
| Humidity | `humidity` | % | |
| Battery voltage | `voltage` | V | diagnostic |
| Wireless quality | — | % | diagnostic |
| Connectivity (per gateway) | `connectivity` | — | diagnostic |

Data source kinds beyond these still produce an entity, using the unit and
precision the API reports, with no device class.

### Why battery is in volts

The API reports battery as a voltage (for example 2.83 V), not a percentage.
Home Assistant's `battery` device class accepts only percentages, and converting
volts to a percentage would need the cell chemistry and empty/full thresholds,
which the API does not expose. Reporting the raw voltage avoids inventing a
number. Set your own threshold in a template binary sensor if you want a
low-battery alert.

### Availability

An entity goes unavailable when its most recent measurement is older than three
of its own reporting intervals. Data sources report at different rates —
temperature every 900 s, battery every 10800 s — so each entity uses its own
interval rather than a single global timeout. A stale sensor disappears rather
than showing a value that is hours out of date.

## Polling and rate limits

Every Sensorist API response carries a `cache-control: max-age` header, and the
API documentation requires clients to honour it. This integration does, in two
layers:

- The API client caches each response until its `max-age` expires. A repeat call
  inside that window is served from memory, with no network request, no matter
  which part of the integration asks.
- The coordinator's poll interval is never shorter than the largest `max-age`
  seen for the measurement endpoint.

In practice that means one poll every 15 minutes, which matches how often the
sensors themselves report. The interval is deliberately not configurable — a
shorter one would either violate the API's caching contract or return identical
data.

## Not included

Historical import into HA long-term statistics, webhook callbacks for button
presses and alarms, gateway reboot, and alarm entities are all out of scope for
now.

## Note for upstreaming

The API client lives at `custom_components/sensorist/api.py` rather than in a
separate PyPI package. Home Assistant core requires integrations to depend on an
external library, so moving this into core would mean splitting `api.py` out
into its own package and listing it in `manifest.json` under `requirements`.

## Development

```bash
uv sync
uv run pytest
uv run ruff check .
uv run mypy custom_components/sensorist
```

`scripts/discover.py` is the throwaway probe used to derive the API shape
recorded in `docs/api-shape.md`. It reads `SENSORIST_EMAIL` and
`SENSORIST_PASSWORD` from the environment and never stores credentials.
