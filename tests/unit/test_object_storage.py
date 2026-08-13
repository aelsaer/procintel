import io

import pytest

from packages.object_storage import (
    LocalObjectStore,
    ObjectTooLargeError,
    S3ObjectStore,
    safe_object_key,
)
from packages.source_clients.raw_store import LocalFilesystemRawStore


class _Body(io.BytesIO):
    pass


class _S3Client:
    def __init__(self):
        self.objects = {}
        self.put_requests = []

    def put_object(self, **request):
        self.put_requests.append(request)
        self.objects[(request["Bucket"], request["Key"])] = request["Body"]

    def get_object(self, *, Bucket, Key):
        payload = self.objects[(Bucket, Key)]
        return {"ContentLength": len(payload), "Body": _Body(payload)}

    def delete_object(self, *, Bucket, Key):
        self.objects.pop((Bucket, Key), None)

    def generate_presigned_url(self, operation, *, Params, ExpiresIn):
        return f"https://signed.test/{Params['Key']}?expires={ExpiresIn}"


def test_safe_object_key_rejects_traversal_and_absolute_paths():
    assert safe_object_key("raw/source/file.json") == "raw/source/file.json"
    with pytest.raises(ValueError):
        safe_object_key("../secret")
    with pytest.raises(ValueError):
        safe_object_key("/absolute")


async def test_local_object_store_is_root_bounded(tmp_path):
    store = LocalObjectStore(tmp_path)
    uri = await store.put("tenant/report.csv", b"a,b\n1,2\n")
    assert await store.get(uri, max_bytes=100) == b"a,b\n1,2\n"
    with pytest.raises(ValueError):
        await store.get(str(tmp_path.parent / "outside"), max_bytes=100)
    with pytest.raises(ValueError):
        await store.presign_get(str(tmp_path.parent / "outside"))


async def test_s3_object_store_round_trip_is_private_and_bounded():
    client = _S3Client()
    store = S3ObjectStore(
        bucket="private-bucket", prefix="documents", client=client, use_threads=False
    )
    uri = await store.put("ab/file.pdf", b"pdf", content_type="application/pdf")

    assert uri == "s3://private-bucket/documents/ab/file.pdf"
    assert client.put_requests[0]["ServerSideEncryption"] == "AES256"
    assert await store.get(uri, max_bytes=3) == b"pdf"
    with pytest.raises(ObjectTooLargeError):
        await store.get(uri, max_bytes=2)
    assert await store.presign_get(uri, expires_seconds=9999) == (
        "https://signed.test/documents/ab/file.pdf?expires=900"
    )
    await store.delete(uri)
    assert client.objects == {}


async def test_s3_store_rejects_foreign_bucket_uri():
    store = S3ObjectStore(bucket="private-bucket", client=_S3Client(), use_threads=False)
    with pytest.raises(ValueError):
        await store.get("s3://attacker-bucket/file", max_bytes=10)


async def test_s3_store_rejects_uri_outside_configured_prefix():
    store = S3ObjectStore(
        bucket="private-bucket",
        prefix="exports",
        client=_S3Client(),
        use_threads=False,
    )
    with pytest.raises(ValueError, match="prefix"):
        await store.presign_get("s3://private-bucket/documents/secret.pdf")


async def test_local_raw_store_rejects_a_traversing_partition(tmp_path):
    store = LocalFilesystemRawStore(tmp_path)

    with pytest.raises(ValueError, match="unsafe object key"):
        await store.put(
            source="KHMDHS",
            resource="notice",
            partition_key="../../outside",
            payload=b"{}",
        )
