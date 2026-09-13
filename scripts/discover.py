#!/usr/bin/env python3
"""Throwaway discovery probe for the Sensorist cloud API.

Phase 0 of the integration spec: the exact JSON field names for gateways,
sensors, data sources and measurements are undocumented, so we dump raw
responses and derive the shape from them instead of guessing.

Stdlib only on purpose -- this never ships with the integration.

Usage:
    SENSORIST_EMAIL=... SENSORIST_PASSWORD=... python3 scripts/discover.py

Optional overrides when the generic structure walk guesses wrong:
    --gateway-id 123 --data-sources 456,789
"""

from __future__ import annotations

import argparse
import base64
import gzip
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

BASE_URL = "https://api.sensorist.com/v1"
SAMPLES_DIR = Path(__file__).parent / "samples"
TIMEOUT = 30

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


class ProbeError(RuntimeError):
    """Any failure while probing the API."""


def redact(obj: Any) -> Any:
    """Recursively replace anything that looks like an email address.

    Names are fine to keep per the spec; only the account identifier goes.
    """
    if isinstance(obj, dict):
        return {k: redact(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(v) for v in obj]
    if isinstance(obj, str):
        return EMAIL_RE.sub("redacted@example.com", obj)
    return obj


def fetch(path: str, params: dict[str, str] | None = None) -> tuple[Any, dict[str, str], str]:
    """GET a path and return (parsed body, response headers, full url)."""
    url = f"{BASE_URL}{path}"
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"

    email = os.environ.get("SENSORIST_EMAIL")
    password = os.environ.get("SENSORIST_PASSWORD")
    if not email or not password:
        raise ProbeError("SENSORIST_EMAIL and SENSORIST_PASSWORD must be set in the environment")

    token = base64.b64encode(f"{email}:{password}".encode()).decode("ascii")
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Basic {token}",
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "User-Agent": "sensorist-discovery/0.1",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            raw = response.read()
            headers = {k.lower(): v for k, v in response.headers.items()}
            status = response.status
    except urllib.error.HTTPError as err:
        body = err.read()
        raise ProbeError(f"HTTP {err.code} for {url}: {body[:500]!r}") from err
    except urllib.error.URLError as err:
        raise ProbeError(f"network error for {url}: {err.reason}") from err

    if headers.get("content-encoding") == "gzip":
        raw = gzip.decompress(raw)

    try:
        body = json.loads(raw)
    except json.JSONDecodeError as err:
        raise ProbeError(f"non-JSON response from {url}: {raw[:500]!r}") from err

    print(f"  HTTP {status} {url}")
    print(f"    cache-control: {headers.get('cache-control', '<none>')}")
    return body, headers, url


def dump(name: str, body: Any, headers: dict[str, str], url: str) -> None:
    """Write a sample file with the redacted body plus the interesting headers."""
    SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "_meta": {
            "url": EMAIL_RE.sub("redacted@example.com", url),
            "cache_control": headers.get("cache-control"),
            "content_type": headers.get("content-type"),
            "date": headers.get("date"),
        },
        "body": redact(body),
    }
    target = SAMPLES_DIR / f"{name}.json"
    target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"    -> {target.relative_to(Path.cwd()) if target.is_relative_to(Path.cwd()) else target}")


def walk_structure(node: Any, path: str = "$", depth: int = 0) -> list[str]:
    """Describe the nesting of lists/dicts so unknown field names become visible."""
    lines: list[str] = []
    indent = "  " * depth
    if isinstance(node, dict):
        scalars = sorted(k for k, v in node.items() if not isinstance(v, (dict, list)))
        lines.append(f"{indent}{path} object keys={scalars}")
        for key, value in node.items():
            if isinstance(value, (dict, list)):
                lines.extend(walk_structure(value, f"{path}.{key}", depth + 1))
    elif isinstance(node, list):
        lines.append(f"{indent}{path} list len={len(node)}")
        if node:
            lines.extend(walk_structure(node[0], f"{path}[0]", depth + 1))
    return lines


def find_id(obj: dict[str, Any]) -> Any:
    """Best-effort id lookup -- the key name is not documented."""
    for key in ("id", "gateway_id", "sensor_id", "data_source_id", "uid", "_id"):
        if key in obj:
            return obj[key]
    return None


def collect_data_sources(gateway: dict[str, Any]) -> list[Any]:
    """Walk a gateway object and gather every leaf id that looks like a data source.

    Data sources are the deepest list-of-objects under a gateway (gateway ->
    sensors -> data sources), so we take ids from the deepest nesting level we
    find rather than trusting a key name.
    """
    found: dict[int, list[Any]] = {}

    def walk(node: Any, depth: int) -> None:
        if isinstance(node, list):
            for item in node:
                walk(item, depth)
        elif isinstance(node, dict):
            identifier = find_id(node)
            child_lists = [v for v in node.values() if isinstance(v, list) and v and isinstance(v[0], dict)]
            if identifier is not None:
                found.setdefault(depth, []).append(identifier)
            for child in child_lists:
                walk(child, depth + 1)

    for value in gateway.values():
        if isinstance(value, list) and value and isinstance(value[0], dict):
            walk(value, 1)

    if not found:
        return []
    deepest = max(found)
    return found[deepest]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gateway-id", help="override the gateway used for the measurements probe")
    parser.add_argument("--data-sources", help="override the comma-separated data source ids")
    args = parser.parse_args()

    try:
        print("GET /users")
        users_body, users_headers, users_url = fetch("/users")
        dump("users", users_body, users_headers, users_url)

        print("GET /gateways (masters only)")
        gw_body, gw_headers, gw_url = fetch("/gateways")
        dump("gateways", gw_body, gw_headers, gw_url)

        print("GET /gateways?with_slaves=1")
        gws_body, gws_headers, gws_url = fetch("/gateways", {"with_slaves": "1"})
        dump("gateways_with_slaves", gws_body, gws_headers, gws_url)

        print("\n--- structure of /gateways?with_slaves=1 ---")
        structure = walk_structure(gws_body)
        print("\n".join(structure))
        (SAMPLES_DIR / "structure.txt").write_text("\n".join(structure) + "\n", encoding="utf-8")

        # Pick a gateway + its data sources for the measurements probe.
        gateways = gws_body if isinstance(gws_body, list) else next(
            (v for v in gws_body.values() if isinstance(v, list)), []
        )
        if args.gateway_id and args.data_sources:
            gateway_id = args.gateway_id
            data_source_ids = args.data_sources.split(",")
        elif gateways:
            gateway = gateways[0]
            gateway_id = find_id(gateway)
            data_source_ids = [str(i) for i in collect_data_sources(gateway)]
        else:
            raise ProbeError("no gateways found in the response; pass --gateway-id/--data-sources")

        print(f"\nusing gateway {gateway_id} with {len(data_source_ids)} data sources: {data_source_ids}")
        if not data_source_ids:
            raise ProbeError("could not derive data source ids; pass --data-sources explicitly")

        print("GET /measurements?type=latest")
        m_body, m_headers, m_url = fetch(
            "/measurements", {"data_sources": ",".join(data_source_ids), "type": "latest"}
        )
        dump("measurements_latest", m_body, m_headers, m_url)

        print("\n--- structure of /measurements?type=latest ---")
        m_structure = walk_structure(m_body)
        print("\n".join(m_structure))

        print("\n--- cache-control summary ---")
        for name, headers in (
            ("/users", users_headers),
            ("/gateways", gw_headers),
            ("/gateways?with_slaves=1", gws_headers),
            ("/measurements?type=latest", m_headers),
        ):
            print(f"  {name}: {headers.get('cache-control', '<none>')}")

    except ProbeError as err:
        print(f"discovery failed: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
