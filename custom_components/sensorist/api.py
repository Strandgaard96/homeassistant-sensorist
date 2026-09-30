"""Thin async client for the Sensorist cloud API.

Kept inside the integration rather than published as a PyPI package. Upstreaming
into Home Assistant core would require splitting it out into its own library;
see README.md.
"""

from __future__ import annotations

import asyncio
import re
import time
from http import HTTPStatus
from typing import Any, Final

import aiohttp
from aiohttp import ClientError, ClientResponseError

from .const import API_BASE_URL, API_TIMEOUT, DEFAULT_MAX_AGE

_MAX_AGE_RE: Final = re.compile(r"max-age\s*=\s*(\d+)", re.IGNORECASE)

type QueryParams = dict[str, str]
type CacheKey = tuple[str, tuple[tuple[str, str], ...]]


class SensoristError(Exception):
    """Base class for every error raised by this client."""


class SensoristAuthError(SensoristError):
    """Credentials were rejected (HTTP 401 or 403)."""


class SensoristConnectionError(SensoristError):
    """The API could not be reached (network failure, timeout or 5xx)."""


class SensoristApiError(SensoristError):
    """The API responded, but not in a way we can use."""


class _CacheEntry:
    """A response body held until its max-age expires."""

    __slots__ = ("body", "expires_at", "max_age")

    def __init__(self, body: dict[str, Any], max_age: int) -> None:
        """Store the body alongside its monotonic expiry."""
        self.body = body
        self.max_age = max_age
        self.expires_at = time.monotonic() + max_age


class SensoristApi:
    """Async Sensorist API client with mandatory max-age caching.

    The API documentation requires clients to honour the cache-control header on
    every response. That is enforced here rather than in the coordinator, so the
    guarantee holds regardless of which caller asks for data or how often.
    """

    def __init__(self, session: aiohttp.ClientSession, email: str, password: str) -> None:
        """Initialise the client with a shared aiohttp session."""
        self._session = session
        self._auth = aiohttp.BasicAuth(email, password)
        self._cache: dict[CacheKey, _CacheEntry] = {}
        self._locks: dict[CacheKey, asyncio.Lock] = {}
        self._max_age_by_path: dict[str, int] = {}

    def max_age_for(self, path: str) -> int:
        """Return the largest max-age seen for a path, defaulting conservatively."""
        return self._max_age_by_path.get(path, DEFAULT_MAX_AGE)

    def invalidate(self, path: str | None = None) -> None:
        """Drop cached responses, for one path or all of them.

        Scoped by default so that refetching the inventory early cannot also
        discard a perfectly valid cached measurement and cost an extra request.
        """
        if path is None:
            self._cache.clear()
            return
        for key in [key for key in self._cache if key[0] == path]:
            del self._cache[key]

    async def async_get_user(self) -> dict[str, Any]:
        """Return the authenticated user object."""
        body = await self._request("/users")
        user = body.get("user")
        if not isinstance(user, dict):
            raise SensoristApiError("no user object in /users response")
        return user

    async def async_get_gateways(self, with_slaves: bool = True) -> list[dict[str, Any]]:
        """Return the gateway inventory, each gateway carrying nested devices."""
        params: QueryParams = {"with_slaves": "1"} if with_slaves else {}
        body = await self._request("/gateways", params)
        gateways = body.get("gateways")
        if not isinstance(gateways, list):
            raise SensoristApiError("no gateways array in /gateways response")
        return gateways

    async def async_get_latest(
        self, gateway_id: int, data_source_ids: list[int]
    ) -> dict[str, dict[str, Any]]:
        """Return the latest measurement per data source, keyed by data source id.

        All ids must belong to the same gateway; the API rejects a mixed request.
        The gateway id is not sent (the endpoint does not take one) but is
        required by the caller's contract to make that constraint explicit.
        """
        if not data_source_ids:
            return {}
        params: QueryParams = {
            "data_sources": ",".join(str(i) for i in sorted(data_source_ids)),
            "type": "latest",
        }
        body = await self._request("/measurements", params)
        measurements = body.get("measurements")
        if not isinstance(measurements, dict):
            raise SensoristApiError(f"no measurements object in response for gateway {gateway_id}")
        return measurements

    async def _request(self, path: str, params: QueryParams | None = None) -> dict[str, Any]:
        """Perform a cached GET, honouring the previous response's max-age."""
        key: CacheKey = (path, tuple(sorted((params or {}).items())))

        cached = self._cached_body(key)
        if cached is not None:
            return cached

        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            # Another task may have populated the cache while we waited.
            cached = self._cached_body(key)
            if cached is not None:
                return cached
            return await self._fetch(key, path, params)

    def _cached_body(self, key: CacheKey) -> dict[str, Any] | None:
        """Return a cached body if its max-age has not expired."""
        entry = self._cache.get(key)
        if entry is None:
            return None
        if time.monotonic() >= entry.expires_at:
            del self._cache[key]
            return None
        return entry.body

    async def _fetch(self, key: CacheKey, path: str, params: QueryParams | None) -> dict[str, Any]:
        """Fetch a path from the API and cache the result."""
        url = f"{API_BASE_URL}{path}"
        timeout = aiohttp.ClientTimeout(total=API_TIMEOUT)

        try:
            response = await self._session.get(
                url,
                params=params,
                auth=self._auth,
                timeout=timeout,
                headers={"Accept": "application/json"},
            )
            async with response:
                status = response.status
                if status in (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN):
                    raise SensoristAuthError(f"credentials rejected by {path} (HTTP {status})")
                if status >= HTTPStatus.INTERNAL_SERVER_ERROR:
                    raise SensoristConnectionError(f"{path} returned HTTP {status}")
                if status != HTTPStatus.OK:
                    raise SensoristApiError(f"{path} returned HTTP {status}")

                max_age = self._parse_max_age(response.headers.get("cache-control"))
                try:
                    body = await response.json()
                except (ClientResponseError, ValueError) as err:
                    raise SensoristApiError(f"{path} returned a non-JSON body") from err
        except TimeoutError as err:
            raise SensoristConnectionError(f"timeout after {API_TIMEOUT}s calling {path}") from err
        except ClientError as err:
            raise SensoristConnectionError(f"network error calling {path}: {err}") from err

        if not isinstance(body, dict):
            raise SensoristApiError(f"{path} returned {type(body).__name__}, expected an object")

        code = body.get("code")
        if isinstance(code, int) and code != HTTPStatus.OK:
            raise SensoristApiError(f"{path} returned application code {code}")

        # The body repeats max_age; trust the header but fall back to the body.
        if max_age is None:
            body_max_age = body.get("max_age")
            max_age = body_max_age if isinstance(body_max_age, int) else DEFAULT_MAX_AGE

        self._max_age_by_path[path] = max(self._max_age_by_path.get(path, 0), max_age)
        self._cache[key] = _CacheEntry(body, max_age)
        return body

    @staticmethod
    def _parse_max_age(header: str | None) -> int | None:
        """Extract max-age seconds from a cache-control header."""
        if not header:
            return None
        match = _MAX_AGE_RE.search(header)
        if match is None:
            return None
        # The pattern only matches digits, so int() cannot fail.
        return int(match.group(1))
