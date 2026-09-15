"""E21/HM4 durable ingestion blob store: the S3 adapter's contract on a stubbed client
(no AWS), its config seam, and the failure-before-catalog-commit ordering in
ingest_source (DB-backed; skips without the compose Postgres)."""

import hashlib

import pytest
from botocore.stub import Stubber
from pydantic import ValidationError
from sqlalchemy import func, select

import app.blob_store as blob_store
from app.blob_store import BlobTooLarge, InMemoryBlobStore, S3BlobStore, source_key
from app.config import Settings

BUCKET = "modelmatch-ingestion-sources-test"


def _stubbed(max_get_bytes=blob_store.MAX_GET_BYTES):
    import boto3

    client = boto3.client("s3", region_name="ap-south-1",
                          aws_access_key_id="stub", aws_secret_access_key="stub")
    store = S3BlobStore(BUCKET, "ap-south-1", client=client, max_get_bytes=max_get_bytes)
    return store, Stubber(client)


def test_put_writes_sse_object_under_the_content_hash_key():
    data = b"# a model card\n"
    key = source_key(hashlib.sha256(data).hexdigest())
    store, stub = _stubbed()
    stub.add_response("put_object", {"ETag": '"x"'}, expected_params={
        "Bucket": BUCKET, "Key": key, "Body": data,
        "ContentType": "application/octet-stream", "ServerSideEncryption": "AES256"})
    with stub:
        assert store.put(key, data) == key
    stub.assert_no_pending_responses()


def test_get_returns_bytes_and_none_for_a_missing_key():
    store, stub = _stubbed()
    stub.add_response("head_object", {"ContentLength": 5}, expected_params={"Bucket": BUCKET, "Key": "sources/a"})
    stub.add_response("get_object", {"Body": _body(b"hello")}, expected_params={"Bucket": BUCKET, "Key": "sources/a"})
    stub.add_client_error("head_object", service_error_code="404", http_status_code=404,
                          expected_params={"Bucket": BUCKET, "Key": "sources/missing"})
    with stub:
        assert store.get("sources/a") == b"hello"
        assert store.get("sources/missing") is None
    stub.assert_no_pending_responses()


def test_get_refuses_oversize_objects_before_reading():
    store, stub = _stubbed(max_get_bytes=4)
    stub.add_response("head_object", {"ContentLength": 5}, expected_params={"Bucket": BUCKET, "Key": "sources/big"})
    with stub, pytest.raises(BlobTooLarge):
        store.get("sources/big")
    stub.assert_no_pending_responses()  # no get_object was attempted


def test_other_client_errors_propagate():
    from botocore.exceptions import ClientError

    store, stub = _stubbed()
    stub.add_client_error("head_object", service_error_code="AccessDenied", http_status_code=403)
    with stub, pytest.raises(ClientError):
        store.get("sources/a")


def test_s3_store_requires_a_bucket():
    with pytest.raises(ValueError):
        S3BlobStore("", "ap-south-1", client=object())


def test_blob_store_setting_accepts_s3_and_rejects_unknown(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "a-real-strong-secret-value")
    monkeypatch.setenv("BLOB_STORE", "s3")
    assert Settings(_env_file=None).blob_store == "s3"
    monkeypatch.setenv("BLOB_STORE", "aws")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_factory_builds_the_s3_adapter_from_settings(monkeypatch):
    class FakeSettings:
        blob_store = "s3"
        s3_bucket = BUCKET
        aws_region = "ap-south-1"

    built = {}

    def fake_init(self, bucket, region, *, client=None, max_get_bytes=None):
        built.update(bucket=bucket, region=region)

    monkeypatch.setattr(blob_store, "get_settings", lambda: FakeSettings())
    monkeypatch.setattr(S3BlobStore, "__init__", fake_init)
    blob_store.get_blob_store.cache_clear()
    try:
        assert isinstance(blob_store.get_blob_store(), S3BlobStore)
        assert built == {"bucket": BUCKET, "region": "ap-south-1"}
    finally:
        blob_store.get_blob_store.cache_clear()
    assert isinstance(InMemoryBlobStore(), blob_store.BlobStore)


class _FailingBlob:
    def put(self, key, data):
        raise RuntimeError("s3 unavailable")

    def get(self, key):
        return None


def test_ingest_fails_before_any_catalog_commit_when_the_blob_put_fails(db_session):
    from pathlib import Path

    from app.ingest.service import ingest_source
    from app.llm.fake import FakeLLMClient
    from app.models import BenchmarkResult, LlmCall, SourceDocument
    from app.schemas.ingest import IngestRequest

    fixtures = Path(__file__).resolve().parent / "fixtures"
    request = IngestRequest(source_text=(fixtures / "sample_source.md").read_text())
    fake = FakeLLMClient(responses=(fixtures / "nova_response.json.txt").read_text())

    with pytest.raises(RuntimeError):
        ingest_source(db_session, request, fake, blob=_FailingBlob())
    db_session.rollback()

    def count(model):
        return db_session.scalar(select(func.count()).select_from(model))

    assert count(SourceDocument) == 0
    assert count(BenchmarkResult) == 0
    # The extraction itself happened and is accounted for (tokens were spent); only
    # the source reference + catalog rows are withheld until the bytes are durable.
    assert count(LlmCall) == 1

    # A retry with a working store then lands the same source exactly once.
    result = ingest_source(db_session, request, FakeLLMClient(responses=fake._responses), blob=InMemoryBlobStore())
    assert result.status == "ingested"
    assert count(SourceDocument) == 1


def _body(data: bytes):
    from botocore.response import StreamingBody
    from io import BytesIO

    return StreamingBody(BytesIO(data), len(data))
