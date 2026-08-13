"""Bounded private-object storage with local and S3 implementations."""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Protocol
from urllib.parse import urlparse


class ObjectTooLargeError(ValueError):
    pass


class ObjectStore(Protocol):
    async def put(self, key: str, payload: bytes, *, content_type: str | None = None) -> str: ...

    async def get(self, uri: str, *, max_bytes: int) -> bytes: ...

    async def delete(self, uri: str) -> None: ...

    async def presign_get(self, uri: str, *, expires_seconds: int = 300) -> str | None: ...


def safe_object_key(value: str) -> str:
    path = PurePosixPath(value.replace("\\", "/"))
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("unsafe object key")
    return "/".join(path.parts)


@dataclass(frozen=True)
class LocalObjectStore:
    root: Path

    async def put(self, key: str, payload: bytes, *, content_type: str | None = None) -> str:
        del content_type
        path = (self.root / safe_object_key(key)).resolve()
        path.relative_to(self.root.resolve())
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_bytes(payload)
        return str(path)

    async def get(self, uri: str, *, max_bytes: int) -> bytes:
        path = Path(uri.removeprefix("file://")).resolve()
        path.relative_to(self.root.resolve())
        if path.stat().st_size > max_bytes:
            raise ObjectTooLargeError("stored object exceeds size limit")
        return path.read_bytes()

    async def delete(self, uri: str) -> None:
        path = Path(uri.removeprefix("file://")).resolve()
        path.relative_to(self.root.resolve())
        path.unlink(missing_ok=True)

    async def presign_get(self, uri: str, *, expires_seconds: int = 300) -> str | None:
        del expires_seconds
        path = Path(uri.removeprefix("file://")).resolve()
        path.relative_to(self.root.resolve())
        if not path.is_file():
            raise FileNotFoundError(path)
        return None


class S3ObjectStore:
    def __init__(
        self,
        *,
        bucket: str,
        prefix: str = "",
        client: Any | None = None,
        use_threads: bool = True,
    ) -> None:
        if not bucket:
            raise ValueError("S3 bucket is required")
        self.bucket = bucket
        self.prefix = safe_object_key(prefix) if prefix else ""
        self.use_threads = use_threads
        if client is None:
            try:
                import boto3
            except ImportError as exc:  # pragma: no cover - packaging guarantees boto3 in production
                raise RuntimeError("boto3 is required for S3 object storage") from exc
            client = boto3.client("s3", endpoint_url=os.environ.get("OBJECT_STORAGE_ENDPOINT") or None)
        self.client = client

    async def _call(self, function: Any, *args: Any, **kwargs: Any) -> Any:
        if self.use_threads:
            return await asyncio.to_thread(function, *args, **kwargs)
        return function(*args, **kwargs)

    def _key(self, value: str) -> str:
        key = safe_object_key(value)
        return f"{self.prefix}/{key}" if self.prefix else key

    def _uri_key(self, uri: str) -> str:
        parsed = urlparse(uri)
        if parsed.scheme != "s3" or parsed.netloc != self.bucket:
            raise ValueError("object URI does not belong to the configured S3 bucket")
        key = safe_object_key(parsed.path.lstrip("/"))
        if self.prefix and not key.startswith(f"{self.prefix}/"):
            raise ValueError("object URI does not belong to the configured S3 prefix")
        return key

    async def put(self, key: str, payload: bytes, *, content_type: str | None = None) -> str:
        object_key = self._key(key)
        request: dict[str, Any] = {
            "Bucket": self.bucket,
            "Key": object_key,
            "Body": payload,
            "ServerSideEncryption": "AES256",
        }
        if content_type:
            request["ContentType"] = content_type
        await self._call(self.client.put_object, **request)
        return f"s3://{self.bucket}/{object_key}"

    async def get(self, uri: str, *, max_bytes: int) -> bytes:
        key = self._uri_key(uri)
        response = await self._call(self.client.get_object, Bucket=self.bucket, Key=key)
        size = int(response.get("ContentLength") or 0)
        if size > max_bytes:
            response["Body"].close()
            raise ObjectTooLargeError("stored object exceeds size limit")
        payload = await self._call(response["Body"].read, max_bytes + 1)
        response["Body"].close()
        if len(payload) > max_bytes:
            raise ObjectTooLargeError("stored object exceeds size limit")
        return payload

    async def delete(self, uri: str) -> None:
        await self._call(
            self.client.delete_object,
            Bucket=self.bucket,
            Key=self._uri_key(uri),
        )

    async def presign_get(self, uri: str, *, expires_seconds: int = 300) -> str | None:
        return await self._call(
            self.client.generate_presigned_url,
            "get_object",
            Params={"Bucket": self.bucket, "Key": self._uri_key(uri)},
            ExpiresIn=max(30, min(expires_seconds, 900)),
        )


def configured_object_store(
    *,
    local_root: str | Path,
    s3_prefix: str,
) -> ObjectStore:
    backend = os.environ.get("OBJECT_STORAGE_BACKEND", "local").casefold()
    if backend == "local":
        return LocalObjectStore(Path(local_root))
    if backend == "s3":
        return S3ObjectStore(
            bucket=os.environ.get("OBJECT_STORAGE_BUCKET", ""),
            prefix=s3_prefix,
        )
    raise RuntimeError(f"unsupported OBJECT_STORAGE_BACKEND: {backend}")
