"""Bounded HTTPS GET only. Pin resolved public IP while retaining TLS hostname checks."""

import hashlib
import http.client
import ipaddress
import socket
import ssl
import time
from dataclasses import dataclass
from urllib.parse import urlsplit


class FetchError(ValueError):
    pass


@dataclass(frozen=True)
class FetchResponse:
    status: int
    body: bytes = b""
    etag: str | None = None
    last_modified: str | None = None


def _resolve(host):
    return sorted(
        {a[4][0] for a in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)}
    )


class _PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, address, timeout):
        super().__init__(host, timeout=timeout, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        sock = socket.create_connection((self.address, 443), self.timeout)
        try:
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except BaseException:
            sock.close()
            raise


def _transport(host, address, path, headers, timeout):
    connection = _PinnedHTTPS(host, address, timeout)
    try:
        connection.request("GET", path, headers=headers)
        response = connection.getresponse()
        original_close = response.close

        def close():
            original_close()
            connection.close()

        response.close = close
        return response
    except BaseException:
        connection.close()
        raise


def _header(value):
    if value is not None and (
        not isinstance(value, str)
        or len(value) > 512
        or any(ord(c) < 32 or ord(c) == 127 for c in value)
    ):
        raise FetchError("invalid conditional header")
    return value


def fetch(
    url,
    *,
    allowed_urls,
    max_bytes,
    expected_hash=None,
    media_types=("application/json", "text/plain"),
    etag=None,
    last_modified=None,
    resolver=_resolve,
    transport=_transport,
    sleep=time.sleep,
    timeout=10,
    attempts=3,
):
    parsed = urlsplit(url)
    if (
        url not in allowed_urls
        or parsed.scheme != "https"
        or not parsed.hostname
        or parsed.port not in (None, 443)
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        raise FetchError("URL not allowed")
    if not 0 < max_bytes <= 8000000 or not 0 < timeout <= 30 or not 1 <= attempts <= 3:
        raise FetchError("invalid fetch limits")
    headers = {
        "Accept": ", ".join(media_types),
        "Accept-Encoding": "identity",
        "User-Agent": "Driftplain-catalog/1",
    }
    if _header(etag):
        headers["If-None-Match"] = etag
    if _header(last_modified):
        headers["If-Modified-Since"] = last_modified
    addresses = resolver(parsed.hostname)
    if not addresses or any(not ipaddress.ip_address(a).is_global for a in addresses):
        raise FetchError("non-public destination")
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query
    for attempt in range(attempts):
        response = None
        try:
            response = transport(parsed.hostname, addresses[0], path, headers, timeout)
            if response.status in (429, 500, 502, 503, 504):
                raise OSError("transient HTTP error")
            if response.status == 304:
                return FetchResponse(
                    304,
                    etag=_header(response.getheader("etag") or etag),
                    last_modified=_header(
                        response.getheader("last-modified") or last_modified
                    ),
                )
            if response.status != 200:
                raise FetchError("HTTP status rejected; redirects are not followed")
            media_type = (
                response.getheader("content-type", "").split(";")[0].strip().lower()
            )
            # GitHub serves raw JSON as text/plain. The adapter still validates its schema.
            if media_type not in media_types:
                raise FetchError("media type rejected")
            if response.getheader("content-encoding", "identity") != "identity":
                raise FetchError("encoded body rejected")
            length = response.getheader("content-length")
            if length is not None and (not length.isdigit() or int(length) > max_bytes):
                raise FetchError("payload too large")
            deadline = time.monotonic() + timeout
            pieces, size = [], 0
            while True:
                if time.monotonic() > deadline:
                    raise FetchError("response deadline exceeded")
                chunk = response.read1(min(65536, max_bytes + 1 - size))
                if not chunk:
                    break
                pieces.append(chunk)
                size += len(chunk)
                if size > max_bytes:
                    raise FetchError("payload too large")
            body = b"".join(pieces)
            if not body or (length is not None and size != int(length)):
                raise FetchError("incomplete body")
            if expected_hash and hashlib.sha256(body).hexdigest() != expected_hash:
                raise FetchError("immutable artifact hash mismatch")
            return FetchResponse(
                200,
                body,
                _header(response.getheader("etag")),
                _header(response.getheader("last-modified")),
            )
        except (OSError, http.client.HTTPException) as exc:
            if attempt + 1 == attempts:
                raise FetchError("fetch attempts exhausted") from exc
        finally:
            if response is not None:
                response.close()
        sleep(0.25 * 2**attempt)
    raise FetchError("fetch failed")
