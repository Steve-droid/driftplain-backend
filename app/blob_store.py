"""Blob-store seam (S5b): persist ingestion source bytes, keep only a key.

Ingestion sources land in blob storage (S3 in-cluster), NOT container disk — the
backend stores only the returned *key* on `source_document.s3_key` (the same
ref-not-payload pattern as app/secret_store.py). `fake` (in-process) is the
dev/test default; `s3` (E21/HM4) is the durable adapter on the ingestion-sources
bucket, selected with BLOB_STORE=s3 and no call-site changes.

The key is derived from the content hash, so re-putting the same source is
idempotent and the key is stable/inspectable — it never encodes the bytes.

Identity for `s3` is whatever the SDK's default chain finds — IRSA on EKS, the
Roles Anywhere process credential at home — never static keys in config.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Optional, Protocol, runtime_checkable

from app.config import get_settings

# Stable, hash-addressed key layout (e.g. "sources/<sha256>"). Deterministic from
# the content hash → the same source always maps to the same key (idempotent put).
_KEY_PREFIX = "sources/"

# Bounded reads: an ingestion source is a model card / benchmark page (kilobytes).
# Anything larger than this is refused rather than streamed into a 1Gi-capped pod.
MAX_GET_BYTES = 8 * 1024 * 1024


def source_key(content_hash: str) -> str:
    """The blob key for a source document, addressed by its content hash."""
    return f"{_KEY_PREFIX}{content_hash}"


class BlobTooLarge(ValueError):
    """A stored object exceeds the bounded-read ceiling (never read into memory)."""


@runtime_checkable
class BlobStore(Protocol):
    def put(self, key: str, data: bytes) -> str:
        """Store `data` under `key`; return the key actually written (the s3_key)."""

    def get(self, key: str) -> Optional[bytes]:
        """Fetch bytes by key (None if absent)."""


class InMemoryBlobStore:
    """Dev/test store: keeps blobs in-process. NOT for production (no persistence) —
    the S3 adapter replaces it. Re-putting the same key overwrites → idempotent."""

    def __init__(self) -> None:
        self._data: dict[str, bytes] = {}

    def put(self, key: str, data: bytes) -> str:
        self._data[key] = data
        return key

    def get(self, key: str) -> Optional[bytes]:
        return self._data.get(key)


class S3BlobStore:
    """Durable store on the ingestion-sources bucket (E21/HM4, HM2-DECISIONS).

    - Content-hash keys (see source_key) → a retried/duplicate put writes identical
      bytes to the same key, so retries are idempotent and never fork the catalog.
    - SSE-S3 requested explicitly on every put (the bucket also defaults to AES256).
    - Bounded: connect/read timeouts, standard SDK retries, and `get` refuses objects
      above MAX_GET_BYTES before reading a byte.
    - The caller (ingest_source) puts BEFORE the source row + catalog rows are
      committed, so a failed put leaves no catalog change behind.
    `client` is injectable so tests use a stubbed client and never touch AWS.
    """

    def __init__(self, bucket: str, region: str, *, client: Any = None,
                 max_get_bytes: int = MAX_GET_BYTES) -> None:
        if not bucket:
            raise ValueError("S3_BUCKET must be set for BLOB_STORE=s3")
        if client is None:
            import boto3  # lazy: only the in-cluster image ships boto3 (--extra bedrock)
            from botocore.config import Config

            client = boto3.client(
                "s3",
                region_name=region,
                config=Config(connect_timeout=5, read_timeout=30,
                              retries={"mode": "standard", "max_attempts": 3}),
            )
        self._bucket = bucket
        self._client = client
        self._max_get_bytes = max_get_bytes

    def put(self, key: str, data: bytes) -> str:
        self._client.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=data,
            ContentType="application/octet-stream",
            ServerSideEncryption="AES256",
        )
        return key

    def get(self, key: str) -> Optional[bytes]:
        from botocore.exceptions import ClientError

        try:
            head = self._client.head_object(Bucket=self._bucket, Key=key)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in ("404", "NoSuchKey", "NotFound"):
                return None
            raise
        length = int(head.get("ContentLength", 0))
        if length > self._max_get_bytes:
            raise BlobTooLarge(f"object {key} is {length} bytes (limit {self._max_get_bytes})")
        body = self._client.get_object(Bucket=self._bucket, Key=key)["Body"]
        data = body.read(self._max_get_bytes + 1)
        if len(data) > self._max_get_bytes:
            raise BlobTooLarge(f"object {key} exceeded the read limit while streaming")
        return data


@lru_cache
def get_blob_store() -> BlobStore:
    """Process-wide singleton, chosen by BLOB_STORE (default: fake)."""
    settings = get_settings()
    kind = settings.blob_store
    if kind == "fake":
        return InMemoryBlobStore()
    if kind == "s3":
        return S3BlobStore(settings.s3_bucket, settings.aws_region)
    raise ValueError(f"Unsupported BLOB_STORE={kind!r} (supported: fake, s3)")
