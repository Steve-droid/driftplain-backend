"""Bounded opaque continuation cursors for the public catalog."""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any


class CatalogCursorError(ValueError):
    pass


def query_hash(parameters: dict[str, Any]) -> str:
    payload = json.dumps(parameters, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def encode_cursor(
    *, resource: str, parameters: dict[str, Any], order: str, last: list[Any]
) -> str:
    payload = {
        "v": 1,
        "resource": resource,
        "queryHash": query_hash(parameters),
        "order": order,
        "last": last,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(
    token: str,
    *,
    resource: str,
    parameters: dict[str, Any],
    order: str,
    last_size: int,
) -> list[Any]:
    if not token or len(token) > 2048:
        raise CatalogCursorError("Invalid catalog cursor")
    try:
        padded = token + "=" * (-len(token) % 4)
        raw = base64.b64decode(padded, altchars=b"-_", validate=True)
        payload = json.loads(raw)
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CatalogCursorError("Invalid catalog cursor") from exc

    if not isinstance(payload, dict) or set(payload) != {
        "v", "resource", "queryHash", "order", "last"
    }:
        raise CatalogCursorError("Invalid catalog cursor")
    if (
        payload["v"] != 1
        or payload["resource"] != resource
        or payload["queryHash"] != query_hash(parameters)
        or payload["order"] != order
        or not isinstance(payload["last"], list)
        or len(payload["last"]) != last_size
    ):
        raise CatalogCursorError("Catalog cursor does not match this query")
    if any(isinstance(value, (dict, list, bool)) for value in payload["last"]):
        raise CatalogCursorError("Invalid catalog cursor position")
    return payload["last"]
