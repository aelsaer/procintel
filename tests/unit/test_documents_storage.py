import asyncio

from services.documents.storage import LocalFilesystemDocumentBlobStore


def test_put_is_content_addressed_and_idempotent(tmp_path):
    store = LocalFilesystemDocumentBlobStore(tmp_path)
    payload = b"%PDF-1.4 some content"

    ref1 = asyncio.run(store.put(payload=payload, mime_type="application/pdf"))
    ref2 = asyncio.run(store.put(payload=payload, mime_type="application/pdf"))

    assert ref1.object_uri == ref2.object_uri
    assert ref1.sha256 == ref2.sha256
    assert ref1.object_uri.endswith(".pdf")
    with open(ref1.object_uri, "rb") as handle:
        assert handle.read() == payload


def test_different_payloads_get_different_object_uris(tmp_path):
    store = LocalFilesystemDocumentBlobStore(tmp_path)
    ref1 = asyncio.run(store.put(payload=b"one", mime_type="application/pdf"))
    ref2 = asyncio.run(store.put(payload=b"two", mime_type="application/pdf"))
    assert ref1.object_uri != ref2.object_uri


def test_unknown_mime_type_falls_back_to_bin_extension(tmp_path):
    store = LocalFilesystemDocumentBlobStore(tmp_path)
    ref = asyncio.run(store.put(payload=b"???", mime_type=None))
    assert ref.object_uri.endswith(".bin")
