# Sensorist API — observed shape

Derived from live responses captured by `scripts/discover.py` on 2026-09-13 against
`https://api.sensorist.com/v1`. Raw samples: `scripts/samples/`, committed as test
fixtures in `tests/fixtures/`.

Account used for discovery: 1 gateway (B16), 2 sensors (S2.0), 4 data sources each.

## Response envelope

Every endpoint wraps its payload in the same object:

```json
{ "code": 200, "max_age": 900, "time": 0.094, "<payload key>": ... }
```

| Field | Meaning |
|---|---|
| `code` | mirrors the HTTP status |
| `max_age` | mirrors the `cache-control: max-age` header, in seconds |
| `time` | server-side processing seconds; ignore |

Payload key is `user`, `gateways` or `measurements` depending on the endpoint.

### Observed `max-age`

| Endpoint | `cache-control` | body `max_age` |
|---|---|---|
| `GET /users` | `max-age=900, private` | 900 |
| `GET /gateways` | `max-age=900, private` | 900 |
| `GET /gateways?with_slaves=1` | `max-age=900, private` | 900 |
| `GET /measurements?type=latest` | `max-age=900, private` | 900 |

**900 s across the board.** The header and the body agree; the client parses the
header (authoritative for caching) and may sanity-check against `max_age`.

## `GET /users`

```json
{
  "user": {
    "id": 14886,
    "email": "...",
    "fname": "Test",
    "lname": "User",
    "country": { "code": "DK" },
    "tz": { "tz": "Europe/Copenhagen" },
    "temp_unit": { "id": 1, "name": "°C" },
    "notification_channel": 15,
    "notification_type": 3,
    "accounts": [
      { "role": "admin", "account": { "id": 14861, "provider": { "id": 1, "name": "Sensorist", "image_phone": null } } }
    ]
  }
}
```

- **Config-flow `unique_id`**: `user.id` (`14886`). Present, stable, integer — no
  need for the lowercased-email fallback, though it stays as a defensive branch.
- `accounts[].account.id` (14861) is a *different* id (billing account). Do not use it.

## `GET /gateways`

The hierarchy has **no distinct key names per level**. Gateway, sensor and data
source are all nested under a recursive `devices` list:

```
gateways[]                     <- gateway   (type.name "B16")
  └── devices[]                <- sensor    (type.name "S2.0")
        └── devices[]          <- data source (type.name "temp"/"humi"/"batt"/"wireless")
              └── devices[]    <- always empty
```

Level is identified by depth, not by key. The integration walks
`gateways[].devices[].devices[]` explicitly.

### Gateway object

| Field | Sample | Notes |
|---|---|---|
| `id` | `7325` | int, primary key |
| `serial` | `"10000000"` | → `DeviceInfo.serial_number` |
| `title` | `"Gateway"` | → device name |
| `firmware` | `"2.43"` | → `sw_version` |
| `type` | `{ "id": 10, "name": "B16" }` | → `model` = `type.name` |
| `disconnected_date` | `null` | **null = online.** Non-null presumed to be a timestamp of disconnection |
| `master_id` | `null` | null on a master; presumably the master's id on a slave |
| `ipv4` | `"203.0.113.10"` | public IP — do **not** expose as an entity; redact in diagnostics |
| `local_ipv4` | `"192.168.1.10"` | LAN IP — same treatment |

No `last_seen` / `last_contact` field exists. Online state is expressible only as
`disconnected_date is None`.

### Sensor object (`gateways[].devices[]`)

| Field | Sample | Notes |
|---|---|---|
| `id` | `57951` | → `DeviceInfo` identifier |
| `serial` | `"aa00bb01"` | → `serial_number` |
| `title` | `"Sensor 2 Rummåling"` | → device name; user-assigned, may be non-ASCII |
| `firmware` | `"1.99"` | → `sw_version` |
| `type` | `{ "id": 4, "name": "S2.0" }` | → `model` |
| `disconnected_date` | `null` | null = connected |
| `current_gateway_id` | `7325` | which gateway it reports through → `via_device` |

**There is no battery field on the sensor.** Battery is a data source (see below).

### Data source object (`gateways[].devices[].devices[]`)

| Field | Sample | Notes |
|---|---|---|
| `id` | `220200` | → entity `unique_id` |
| `title` | `"Temperature"` | user-visible label, English in this account |
| `serial` | `"t5p0xxxx"` | **masked by the API itself**, literal `xxxx`. Useless; ignore |
| `interval` | `900` | **reporting interval in seconds — varies per kind** |
| `subtype` | `"digital"` or `null` | only `"digital"` observed on temp/humi |
| `type.id` | `5` | stable numeric kind identifier — **key the entity mapping on this** |
| `type.name` | `"temp"` | short kind slug |
| `type.precision` | `1` | decimal places → `suggested_display_precision` |
| `type.capability` | `72` | meaning undocumented and not inferable; ignore |
| `type.unit` | `{ "id": 1, "name": "°C" }` | unit string is display-ready |

### Full set of kinds present in this account

| `type.id` | `type.name` | `title` | unit id/name | `precision` | `interval` (s) | sample value |
|---|---|---|---|---|---|---|
| 5 | `temp` | Temperature | 1 / `°C` | 1 | 900 | 20.34 |
| 4 | `humi` | Humidity | 2 / `%` | 0 | 900 | 70.8 |
| 1 | `batt` | Battery | 3 / `v` | 2 | 10800 | 2.83 |
| 7 | `wireless` | Wireless | 6 / `%` | 0 | 1800 | 100 |

Unit ids are not contiguous (1, 2, 3, 6), so unobserved units exist. Map on
`type.id`, and fall back to passing `type.unit.name` straight through for
anything unrecognised.

## `GET /measurements?data_sources=<ids>&type=latest`

`measurements` is an **object keyed by data-source id as a string**, not an array:

```json
{
  "measurements": {
    "220200": { "date": "2026-09-13T16:59:04+0000", "value": 20.34, "type": 1, "seq": 1789318746316133, "min": null, "max": null }
  }
}
```

| Field | Notes |
|---|---|
| `value` | int or float |
| `date` | UTC, but offset is `+0000` — **no colon**, so not strict ISO 8601. `datetime.fromisoformat` handles it on Python 3.11+; `homeassistant.util.dt.parse_datetime` also accepts it |
| `type` | `1` on all 8 data sources here. Types 5/20/30 documented but **not observed** |
| `seq` | microsecond-ish monotonic sequence number; usable as a change detector |
| `min` / `max` | `null` for `type=latest`; presumably populated for aggregated/grouped rows |

A data source with no data is expected to be absent from the map — not observed,
so the code must treat a missing key as "no measurement" rather than assume it.

## Discrepancies with the spec (API wins)

1. **Battery is volts, not percent.** `type.id 1` reports `unit.name "v"` with
   values 2.79 / 2.83 and `precision 2`. Home Assistant's `BATTERY` device class
   requires `%`, so the spec's `BATTERY` / `%` mapping is not usable. Plan:
   `SensorDeviceClass.VOLTAGE`, `UnitOfElectricPotential.VOLT`, precision 2,
   `EntityCategory.DIAGNOSTIC`. No percentage conversion — the cell chemistry and
   empty/full thresholds are unknown, so any mapping to % would be invented.

2. **Staleness cannot use the coordinator interval.** The spec says unavailable
   when the measurement is older than 3× the coordinator interval. With a 900 s
   coordinator floor that is 2700 s, but battery reports every 10800 s and the
   captured battery reading was already ~2.8 h old — every battery entity would
   be permanently unavailable. Plan: threshold is
   `3 × max(data_source.interval, coordinator interval)`, per data source.

3. **Poll interval floor is 900 s, not 300 s.** The spec's 5-minute default is
   below every observed `max-age`, so the effective `update_interval` is 900 s.
   This also matches the sensors' own 900 s reporting interval — faster polling
   would return identical data.

4. **`with_slaves=1` changed nothing.** Byte-identical response. This account has
   a single master gateway with no slaves, so the parameter's behaviour is
   unverified. Harmless to keep sending.

5. **No gateway "last seen".** Only `disconnected_date` (null when online). A
   `TIMESTAMP` sensor would read `unknown` during normal operation, so the
   natural fit is a `BinarySensorDeviceClass.CONNECTIVITY` entity — which means
   adding `binary_sensor.py`, a file the spec's tree does not list.

6. **`wireless` has no good device class.** It is a percentage signal quality.
   HA's `SIGNAL_STRENGTH` device class only permits dB/dBm, so this becomes a
   plain `MEASUREMENT` sensor with unit `%`, `EntityCategory.DIAGNOSTIC`, no
   device class.

7. **Gateways expose public and local IP addresses.** `ipv4` / `local_ipv4` must
   be redacted in `diagnostics.py` and never surfaced as entity state.

## Open questions

- Measurement `type` values 5 / 20 / 30 were never observed; the ignore-20/30
  logic is written to spec and covered by synthetic fixtures only.
- `type.capability` (72 / 80 / 4) has no documented meaning.
- Behaviour of a data source that has never reported (absent key vs. `type: 20`).
