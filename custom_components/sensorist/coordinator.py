"""Data update coordinator for the Sensorist integration."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    SensoristApi,
    SensoristAuthError,
    SensoristConnectionError,
    SensoristError,
)
from .const import (
    DEFAULT_MAX_AGE,
    DOMAIN,
    INVENTORY_REFRESH_INTERVAL,
    REAL_MEASUREMENT_TYPES,
    UPDATE_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)


@contextmanager
def _mapped_errors() -> Iterator[None]:
    """Translate client exceptions into the ones Home Assistant expects."""
    try:
        yield
    except SensoristAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except SensoristConnectionError as err:
        raise UpdateFailed(str(err)) from err
    except SensoristError as err:
        raise UpdateFailed(f"unexpected API response: {err}") from err


type SensoristConfigEntry = ConfigEntry[SensoristDataUpdateCoordinator]


@dataclass(frozen=True, slots=True)
class SensoristGateway:
    """A Sensorist gateway, the device that talks to the cloud."""

    id: int
    title: str
    serial: str | None
    firmware: str | None
    model: str | None
    master_id: int | None
    disconnected_date: datetime | None

    @property
    def is_connected(self) -> bool:
        """Return whether the gateway is currently online.

        The API exposes no last-seen field; a null disconnected_date is the only
        signal that the gateway is up.
        """
        return self.disconnected_date is None


@dataclass(frozen=True, slots=True)
class SensoristSensor:
    """A physical sensor unit reporting through a gateway."""

    id: int
    title: str
    serial: str | None
    firmware: str | None
    model: str | None
    gateway: SensoristGateway


@dataclass(frozen=True, slots=True)
class SensoristDataSource:
    """One measured quantity on one sensor."""

    id: int
    title: str
    kind_id: int | None
    kind_name: str | None
    unit: str | None
    precision: int | None
    interval: int | None
    sensor: SensoristSensor


@dataclass(frozen=True, slots=True)
class SensoristMeasurement:
    """A single reading for a data source."""

    value: float
    timestamp: datetime | None
    measurement_type: int


@dataclass(slots=True)
class SensoristData:
    """Everything one coordinator refresh produced."""

    gateways: dict[int, SensoristGateway] = field(default_factory=dict)
    data_sources: dict[int, SensoristDataSource] = field(default_factory=dict)
    measurements: dict[int, SensoristMeasurement] = field(default_factory=dict)


class SensoristDataUpdateCoordinator(DataUpdateCoordinator[SensoristData]):
    """Poll the Sensorist cloud for the whole account."""

    config_entry: SensoristConfigEntry

    def __init__(self, hass: HomeAssistant, entry: SensoristConfigEntry) -> None:
        """Initialise the coordinator and its API client."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            config_entry=entry,
            update_interval=UPDATE_INTERVAL,
        )
        self.api = SensoristApi(
            async_get_clientsession(hass),
            entry.data[CONF_EMAIL],
            entry.data[CONF_PASSWORD],
        )
        self._gateways: dict[int, SensoristGateway] = {}
        self._data_sources: dict[int, SensoristDataSource] = {}
        self._inventory_age: datetime | None = None

    async def _async_setup(self) -> None:
        """Fetch the inventory once, before the first data refresh.

        Keeping this out of _async_update_data means the hourly inventory
        refresh is the only thing that ever refetches it.
        """
        with _mapped_errors():
            await self._async_refresh_inventory()

    async def _async_update_data(self) -> SensoristData:
        """Refresh the inventory if it is due, then fetch the latest values.

        A steady-state poll is one /measurements request per gateway. The
        inventory is only refetched hourly, and never in response to the
        measurements themselves: the request only ever names data sources we
        already know about, so an unknown id cannot come back in the reply.
        Genuinely new hardware is picked up by the hourly refresh.
        """
        with _mapped_errors():
            await self._async_refresh_inventory()
            measurements = await self._async_fetch_measurements()

        self._adjust_update_interval()

        return SensoristData(
            gateways=dict(self._gateways),
            data_sources=dict(self._data_sources),
            measurements=measurements,
        )

    async def _async_refresh_inventory(self, force: bool = False) -> None:
        """Refetch the gateway inventory, at most hourly unless forced."""
        now = dt_util.utcnow()
        if (
            not force
            and self._inventory_age is not None
            and now - self._inventory_age < INVENTORY_REFRESH_INTERVAL
        ):
            return

        if force:
            # Bypass the client's max-age cache for the inventory only, so a
            # cached measurement is not thrown away along with it.
            self.api.invalidate("/gateways")

        raw_gateways = await self.api.async_get_gateways(with_slaves=True)

        gateways: dict[int, SensoristGateway] = {}
        data_sources: dict[int, SensoristDataSource] = {}

        for raw_gateway in raw_gateways:
            gateway = _parse_gateway(raw_gateway)
            if gateway is None:
                continue
            gateways[gateway.id] = gateway
            for raw_sensor in _devices(raw_gateway):
                sensor = _parse_sensor(raw_sensor, gateway)
                if sensor is None:
                    continue
                for raw_source in _devices(raw_sensor):
                    source = _parse_data_source(raw_source, sensor)
                    if source is not None:
                        data_sources[source.id] = source

        self._gateways = gateways
        self._data_sources = data_sources
        self._inventory_age = now

    async def _async_fetch_measurements(self) -> dict[int, SensoristMeasurement]:
        """Fetch the latest reading for every data source, one call per gateway.

        The API rejects a request mixing data sources from different gateways,
        so gateways are fetched separately but concurrently.
        """
        by_gateway: dict[int, list[int]] = {}
        for source in self._data_sources.values():
            by_gateway.setdefault(source.sensor.gateway.id, []).append(source.id)

        if not by_gateway:
            return {}

        gateway_ids = list(by_gateway)
        results = await asyncio.gather(
            *(
                self.api.async_get_latest(gateway_id, by_gateway[gateway_id])
                for gateway_id in gateway_ids
            )
        )

        previous = self.data.measurements if self.data else {}
        measurements: dict[int, SensoristMeasurement] = {}

        for raw_measurements in results:
            for raw_id, raw in raw_measurements.items():
                source_id = _as_int(raw_id)
                if source_id is None or not isinstance(raw, dict):
                    continue
                measurement = _parse_measurement(raw)
                if measurement is None:
                    # Blank or interpolated row: no new data, so the previous
                    # reading stands rather than the entity dropping out.
                    if (kept := previous.get(source_id)) is not None:
                        measurements[source_id] = kept
                    continue
                measurements[source_id] = measurement

        return measurements

    def _adjust_update_interval(self) -> None:
        """Never poll faster than the API's own max-age allows."""
        max_age = max(
            DEFAULT_MAX_AGE,
            self.api.max_age_for("/measurements"),
            self.api.max_age_for("/gateways"),
        )
        interval = timedelta(seconds=max_age)
        if interval != self.update_interval:
            _LOGGER.debug("Adjusting update interval to %s per API max-age", interval)
            self.update_interval = interval


def _devices(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the nested devices list, which is how the API nests every level."""
    devices = raw.get("devices")
    if not isinstance(devices, list):
        return []
    return [device for device in devices if isinstance(device, dict)]


def _as_int(value: Any) -> int | None:
    """Coerce an API id to int, tolerating the string keys used by measurements."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _as_dict(value: Any) -> dict[str, Any]:
    """Return a nested object, or an empty dict when it is absent or malformed."""
    return value if isinstance(value, dict) else {}


def _as_str(value: Any) -> str | None:
    """Return a non-empty string, or None."""
    return value if isinstance(value, str) and value else None


def _parse_datetime(value: Any) -> datetime | None:
    """Parse an API timestamp.

    The API returns UTC offsets without a colon ("+0000"), which is not strict
    ISO 8601 but which parse_datetime accepts.
    """
    if not isinstance(value, str) or not value:
        return None
    return dt_util.parse_datetime(value)


def _parse_gateway(raw: dict[str, Any]) -> SensoristGateway | None:
    """Build a gateway from its API object, or None if it has no usable id."""
    gateway_id = _as_int(raw.get("id"))
    if gateway_id is None:
        return None
    raw_type = _as_dict(raw.get("type"))
    return SensoristGateway(
        id=gateway_id,
        title=_as_str(raw.get("title")) or f"Gateway {gateway_id}",
        serial=_as_str(raw.get("serial")),
        firmware=_as_str(raw.get("firmware")),
        model=_as_str(raw_type.get("name")),
        master_id=_as_int(raw.get("master_id")),
        disconnected_date=_parse_datetime(raw.get("disconnected_date")),
    )


def _parse_sensor(raw: dict[str, Any], gateway: SensoristGateway) -> SensoristSensor | None:
    """Build a sensor from its API object, or None if it has no usable id."""
    sensor_id = _as_int(raw.get("id"))
    if sensor_id is None:
        return None
    raw_type = _as_dict(raw.get("type"))
    return SensoristSensor(
        id=sensor_id,
        title=_as_str(raw.get("title")) or f"Sensor {sensor_id}",
        serial=_as_str(raw.get("serial")),
        firmware=_as_str(raw.get("firmware")),
        model=_as_str(raw_type.get("name")),
        gateway=gateway,
    )


def _parse_data_source(raw: dict[str, Any], sensor: SensoristSensor) -> SensoristDataSource | None:
    """Build a data source from its API object, or None if it has no usable id."""
    source_id = _as_int(raw.get("id"))
    if source_id is None:
        return None
    raw_type = _as_dict(raw.get("type"))
    raw_unit = _as_dict(raw_type.get("unit"))
    return SensoristDataSource(
        id=source_id,
        title=_as_str(raw.get("title")) or f"Data source {source_id}",
        kind_id=_as_int(raw_type.get("id")),
        kind_name=_as_str(raw_type.get("name")),
        unit=_as_str(raw_unit.get("name")),
        precision=_as_int(raw_type.get("precision")),
        interval=_as_int(raw.get("interval")),
        sensor=sensor,
    )


def _parse_measurement(raw: dict[str, Any]) -> SensoristMeasurement | None:
    """Build a measurement, or None when the row carries no real reading."""
    measurement_type = _as_int(raw.get("type"))
    if measurement_type not in REAL_MEASUREMENT_TYPES:
        return None
    value = raw.get("value")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return SensoristMeasurement(
        value=float(value),
        timestamp=_parse_datetime(raw.get("date")),
        measurement_type=measurement_type,
    )
