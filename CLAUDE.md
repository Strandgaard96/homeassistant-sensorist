# CLAUDE.md

Home Assistant custom integration (HACS) for Sensorist environment sensors via the cloud REST API (`https://api.sensorist.com/v1`). Config-flow based, `cloud_polling`, quality scale `silver`. Code in `custom_components/sensorist/`.

## Commands

```bash
uv sync
uv run pytest                              # pytest-homeassistant-custom-component (aioclient_mock)
uv run ruff check .
uv run mypy custom_components/sensorist    # strict
```

All three clean before committing.

## Releases

Release Please (`.github/workflows/release.yml`) keeps a release PR open on `main`. Merging it bumps `manifest.json` + `pyproject.toml`, writes `CHANGELOG.md`, and publishes the `vX.Y.Z` GitHub release that HACS installs. So:

- Commit messages must be Conventional Commits: `fix:` → patch, `feat:` → minor, `feat!:` → minor while < 1.0. `docs:`/`test:`/`chore:`/`ci:` don't release.
- Never edit `version` by hand.

## Architecture

- `api.py` — `SensoristApi`. Caches every response until its `cache-control: max-age` expires; tracks max-age per path (`max_age_for`). Errors: `SensoristAuthError` / `SensoristConnectionError` / `SensoristApiError`.
- `coordinator.py` — `SensoristDataUpdateCoordinator` + parsed dataclasses (`SensoristGateway/Sensor/DataSource/Measurement`, `SensoristData`). Inventory (`/gateways?with_slaves=1`) refetched at most every `INVENTORY_REFRESH_INTERVAL` (1h); measurements = one `/measurements` request per gateway, concurrent. `_adjust_update_interval` keeps poll interval ≥ largest observed max-age.
- `entity.py` — device info: one device per sensor, linked `via` its gateway device.
- `sensor.py` / `binary_sensor.py` — measurement sensors; gateway connectivity binary sensor.
- `diagnostics.py` — must redact credentials and identifiers.

## Rules

- **Honour max-age.** API docs require it. Never make the poll interval shorter or configurable.
- **Describe entities from the API, not a fixed table.** Unit, precision, device class come from each data source; Fahrenheit accounts and unknown source kinds must keep working (`tests/test_other_accounts.py`).
- Battery is reported in **volts** (`voltage` device class). Don't convert to percent — the API gives no chemistry or thresholds.
- Availability: stale once the latest measurement is older than 3× that data source's own interval.
- `api.py` stays in-tree (`requirements: []`). Upstreaming to HA core would require splitting it into a PyPI package.

## Fixtures and API shape

- `docs/api-shape.md` is the reference for the observed response shape. Raw samples in `scripts/samples/`, copied to `tests/fixtures/`.
- Samples are **redacted** (email, name, serials, IPs). Keep them redacted when regenerating.
- `scripts/discover.py` — stdlib-only throwaway probe; reads `SENSORIST_EMAIL` / `SENSORIST_PASSWORD` from env, never stores credentials. Not shipped.
