"""Constants for the Sensorist integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "sensorist"
MANUFACTURER: Final = "Sensorist"

API_BASE_URL: Final = "https://api.sensorist.com/v1"
API_TIMEOUT: Final = 10

# Every observed endpoint returns "cache-control: max-age=900". Polling faster
# than that is forbidden by the API docs and would return identical data anyway,
# since the sensors themselves report at 900 s intervals.
DEFAULT_MAX_AGE: Final = 900
UPDATE_INTERVAL: Final = timedelta(seconds=DEFAULT_MAX_AGE)

# The gateway inventory changes only when hardware is added or renamed.
INVENTORY_REFRESH_INTERVAL: Final = timedelta(hours=1)

# A measurement is stale once this many reporting intervals have passed without
# a fresh value. Data sources report at wildly different rates (900 s for
# temperature, 10800 s for battery), so the entity uses its own interval rather
# than the coordinator's.
STALE_INTERVAL_FACTOR: Final = 3

# Measurement "type" field. Only regular readings and button pushes carry a
# real value; blank and interpolated rows are placeholders to be ignored.
MEASUREMENT_TYPE_REGULAR: Final = 1
MEASUREMENT_TYPE_BUTTON: Final = 5
MEASUREMENT_TYPE_GROUPED: Final = 10
MEASUREMENT_TYPE_BLANK: Final = 20
MEASUREMENT_TYPE_INTERPOLATED: Final = 30
REAL_MEASUREMENT_TYPES: Final = frozenset({MEASUREMENT_TYPE_REGULAR, MEASUREMENT_TYPE_BUTTON})

# Data source "type.id" values observed during discovery. Keyed on the numeric
# id rather than type.name because the id is the stable identifier.
KIND_BATTERY: Final = 1
KIND_HUMIDITY: Final = 4
KIND_TEMPERATURE: Final = 5
KIND_WIRELESS: Final = 7

# Gateway fields that leak network topology; never surfaced as state, redacted
# in diagnostics.
SENSITIVE_GATEWAY_FIELDS: Final = ("ipv4", "local_ipv4")
