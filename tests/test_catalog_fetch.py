"""No-network fetch policy tests at the HTTPS transport boundary."""

import io
import pytest
from app.catalog.imports.fetch import fetch, FetchError

URL = "https://public.example/result.json"


class Response(io.BytesIO):
    def __init__(self, status=200, body=b"{}", headers=None):
        super().__init__(body)
        self.status = status
        self.headers = {"content-type": "application/json", **(headers or {})}

    def getheader(self, name, default=None):
        return self.headers.get(name.lower(), default)


class Transport:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def __call__(self, host, ip, path, headers, timeout):
        self.calls.append((host, ip, path, headers))
        return next(self.responses)


def run(transport, **kwargs):
    return fetch(
        URL,
        allowed_urls=(URL,),
        max_bytes=10,
        transport=transport,
        resolver=lambda _: ["93.184.216.34"],
        sleep=lambda _: None,
        **kwargs,
    )


def test_conditional_requests_and_hash():
    transport = Transport([Response(headers={"etag": '"v1"'})])
    result = run(transport, etag='"v0"')
    assert result.body == b"{}" and result.etag == '"v1"'
    assert transport.calls[0][3]["If-None-Match"] == '"v0"'


@pytest.mark.parametrize(
    "response",
    [
        Response(body=b" " * 11),
        Response(headers={"Content-Type": "text/html", "content-type": "text/html"}),
        Response(status=302, headers={"location": "http://127.0.0.1/"}),
        Response(headers={"content-length": "999"}),
    ],
)
def test_bad_responses_fail_closed(response):
    with pytest.raises(FetchError):
        run(Transport([response]))


def test_unapproved_url_and_private_dns_never_reach_transport():
    transport = Transport([])
    for url, addresses in [
        ("https://other.example/", ["93.184.216.34"]),
        (URL, ["127.0.0.1"]),
        (URL, ["93.184.216.34", "::1"]),
    ]:
        with pytest.raises(FetchError):
            fetch(
                url,
                allowed_urls=(URL,),
                max_bytes=10,
                transport=transport,
                resolver=lambda _: addresses,
            )
    assert not transport.calls


def test_retry_only_transient_errors_and_reject_hash_mismatch():
    transport = Transport([Response(status=503), Response()])
    assert run(transport).status == 200 and len(transport.calls) == 2
    with pytest.raises(FetchError):
        run(Transport([Response()]), expected_hash="0" * 64)


def test_304_and_header_injection():
    assert run(Transport([Response(status=304)]), etag='"v1"').status == 304
    with pytest.raises(FetchError):
        run(Transport([]), etag="a\r\nAuthorization: x")
