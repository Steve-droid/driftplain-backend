"""Fetch reviewed fixed locations; never discover or execute paths from remote payloads."""

import base64
import hashlib
import json

from app.catalog.imports.fetch import FetchResponse, fetch


def acquire_source(spec, *, fetcher=None, state=None):
    contract = spec["importContract"]
    if contract["mode"] == "reviewed_manifest":
        raise ValueError("reviewed source requires an explicitly reviewed manifest")
    fetcher = fetcher or fetch
    if contract["mode"] == "automatic_structured":
        return fetcher(
            contract["url"],
            allowed_urls=(contract["url"],),
            max_bytes=contract["max_bytes"],
            expected_hash=contract.get("sha256"),
            media_types=tuple(
                contract.get("media_types", ("application/json", "text/plain"))
            ),
            etag=state.etag if state else None,
            last_modified=state.last_modified if state else None,
        )
    # All required artifacts are pinned to one reviewed immutable revision. Conditional
    # headers on individual files cannot stand in for a complete accepted bundle.
    files = []
    total = 0
    for descriptor in contract["files"]:
        response = fetcher(
            descriptor["url"],
            allowed_urls=(descriptor["url"],),
            max_bytes=descriptor["byte_count"],
            expected_hash=descriptor["sha256"],
            media_types=("application/json", "text/plain", "text/csv"),
        )
        body = response.body
        if (
            response.status != 200
            or len(body) != descriptor["byte_count"]
            or hashlib.sha256(body).hexdigest() != descriptor["sha256"]
        ):
            raise ValueError("incomplete or corrupt artifact bundle")
        total += len(body)
        if total > contract["max_bytes"]:
            raise ValueError("bundle outside total byte budget")
        files.append(
            {**descriptor, "content_base64": base64.b64encode(body).decode("ascii")}
        )
    document = {
        "schema_version": 1,
        "source_id": spec["id"],
        "revision": contract["revision"],
        "files": files,
    }
    raw = (json.dumps(document, indent=2, ensure_ascii=False) + "\n").encode()
    if len(raw) > contract["max_bytes"]:
        raise ValueError("bundle envelope outside byte budget")
    return FetchResponse(200, raw)
