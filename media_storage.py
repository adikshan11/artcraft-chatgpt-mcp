# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Adithya Shankaran

"""Per-user media storage for the hosted ArtCraft Studio service.

Every object lives under ``users/<user-id>/`` so one signed-in user can never
name another user's files. Media is ``media/<media-id>``. The ArtCraft grant and
the session clock are separate objects in that same prefix. Production uses
S3-compatible object storage (Cloudflare R2 or Backblaze B2); tests and local
development use a plain directory.
"""

import hashlib
import mimetypes
import os
import re
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

# Extensions run up to 11 characters for Craft project files such as .vectorcraft.
MEDIA_ID_PATTERN = re.compile(r"^[0-9a-f]{32}\.[a-z0-9]{2,11}$")
USER_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
# The service's own records (usage counts) live under ops/, apart from users/.
OPS_KEY_PATTERN = re.compile(r"^ops/[a-z]+/[0-9T-]+/[0-9a-f]{12}\.json$")
DOWNLOAD_URL_TTL_SECONDS = 3600
UPLOAD_URL_TTL_SECONDS = 900

ALLOWED_EXTENSIONS = {
    "image": {"png", "jpg", "jpeg", "webp", "gif", "bmp", "tiff"},
    "video": {"mp4", "mov", "webm", "mkv", "m4v"},
    "audio": {"mp3", "wav", "m4a", "aac", "ogg", "flac", "aiff", "mid"},
    "pdf": {"pdf"},
    # Word-processor files for the hosted Craft apps. HTML and SVG are left out on
    # purpose: a download link that renders active content is an XSS risk.
    "document": {"docx", "doc", "odt", "rtf", "md", "txt", "tex"},
    "presentation": {"pptx", "deckcraft"},
    # Native Craft project files and other layered design formats.
    "project": {"pcraft", "vectorcraft", "psd", "ai", "designcraft", "idml", "figcraft", "fig", "scraft"},
}
EXTENSION_KIND = {ext: kind for kind, exts in ALLOWED_EXTENSIONS.items() for ext in exts}


class StorageError(RuntimeError):
    """A storage operation failed or was not allowed."""


@dataclass
class StoredObject:
    media_id: str
    kind: str
    size_bytes: int
    modified_at: str


def user_id_for(issuer: str, subject: str) -> str:
    """Stable, opaque per-user id derived from the OAuth issuer and subject."""
    if not subject:
        raise StorageError("The access token has no subject; cannot identify the user.")
    return hashlib.sha256(f"{issuer}|{subject}".encode()).hexdigest()[:32]


def media_kind(media_id: str) -> str:
    return EXTENSION_KIND[media_id.rsplit(".", 1)[1]]


def normalize_extension(filename_or_ext: str) -> str:
    ext = filename_or_ext.rsplit(".", 1)[-1].lower().strip()
    if ext not in EXTENSION_KIND:
        allowed = ", ".join(sorted(EXTENSION_KIND))
        raise StorageError(f"Unsupported file type '.{ext}'. Allowed types: {allowed}.")
    return ext


def new_media_id(ext: str) -> str:
    return f"{uuid.uuid4().hex}.{normalize_extension(ext)}"


def validate_media_id(media_id: str) -> str:
    if not MEDIA_ID_PATTERN.match(media_id or "") or media_id.rsplit(".", 1)[1] not in EXTENSION_KIND:
        raise StorageError(f"'{media_id}' is not a valid media id.")
    return media_id


def _user_prefix(user_id: str) -> str:
    if not USER_ID_PATTERN.match(user_id or ""):
        raise StorageError("Invalid user id.")
    return f"users/{user_id}"


def object_key(user_id: str, media_id: str) -> str:
    return f"{_user_prefix(user_id)}/media/{validate_media_id(media_id)}"


# The grant and the session clock are not media, so a media sweep cannot mistake them for files.
PRIVATE_KEY_PATTERN = re.compile(
    r"^users/[0-9a-f]{32}/(?:grant/exchanged-token|session/session\.json)$"
)


def grant_key(user_id: str) -> str:
    return validate_private_key(f"{_user_prefix(user_id)}/grant/exchanged-token")


def session_key(user_id: str) -> str:
    return validate_private_key(f"{_user_prefix(user_id)}/session/session.json")


def validate_private_key(key: str) -> str:
    if not PRIVATE_KEY_PATTERN.match(key or ""):
        raise StorageError("Invalid record key.")
    return key


def ops_key(key: str) -> str:
    if not OPS_KEY_PATTERN.match(key):
        raise StorageError("Invalid record key.")
    return key


def reject_connector_token(*parts) -> None:
    """Storage calls use the bucket key, never the connector token."""
    from connector_auth import client_token_in_context

    client = client_token_in_context()
    if not client:
        return
    for part in parts:
        if isinstance(part, str) and client in part:
            raise StorageError("Refusing to send the connector token to storage.")
        if isinstance(part, (bytes, bytearray)) and client.encode() in part:
            raise StorageError("Refusing to send the connector token to storage.")


def content_type_for(media_id: str) -> str:
    return mimetypes.guess_type(media_id)[0] or "application/octet-stream"


class LocalStorage:
    """Directory-backed storage for tests and local development."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def _path(self, user_id, media_id):
        return self.root / object_key(user_id, media_id)

    def put_file(self, user_id, media_id, source: Path):
        target = self._path(user_id, media_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)

    def get_file(self, user_id, media_id, target: Path):
        source = self._path(user_id, media_id)
        if not source.is_file():
            raise StorageError(f"Media '{media_id}' was not found.")
        shutil.copyfile(source, target)

    def delete(self, user_id, media_id):
        source = self._path(user_id, media_id)
        if not source.is_file():
            raise StorageError(f"Media '{media_id}' was not found.")
        source.unlink()

    def list(self, user_id) -> list[StoredObject]:
        folder = self.root / "users" / user_id / "media"
        if not folder.is_dir():
            return []
        items = []
        for path in folder.iterdir():
            if MEDIA_ID_PATTERN.match(path.name):
                stat = path.stat()
                modified = datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat()
                items.append(StoredObject(path.name, media_kind(path.name), stat.st_size, modified))
        return sorted(items, key=lambda item: item.modified_at, reverse=True)

    def put_private(self, key: str, data: bytes):
        target = self.root / validate_private_key(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    def get_private(self, key: str) -> bytes | None:
        source = self.root / validate_private_key(key)
        return source.read_bytes() if source.is_file() else None

    def delete_private(self, key: str):
        (self.root / validate_private_key(key)).unlink(missing_ok=True)

    def list_user_ids(self) -> "list[str]":
        folder = self.root / "users"
        if not folder.is_dir():
            return []
        return sorted(path.name for path in folder.iterdir() if path.is_dir() and USER_ID_PATTERN.match(path.name))

    def delete_user(self, user_id: str) -> int:
        """Delete everything stored under ``users/<user_id>/``. Returns how many files."""
        folder = self.root / _user_prefix(user_id)
        if not folder.is_dir():
            return 0
        resolved = folder.resolve()
        if not resolved.is_relative_to(self.root.resolve()):
            raise StorageError("Invalid user id.")
        count = sum(1 for path in folder.rglob("*") if path.is_file() and not path.is_symlink())
        shutil.rmtree(folder)
        return count

    def download_url(self, user_id, media_id):
        return self._path(user_id, media_id).resolve().as_uri()

    def put_record(self, key: str, data: bytes):
        target = self.root / ops_key(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    def get_record(self, key: str) -> bytes | None:
        source = self.root / ops_key(key)
        return source.read_bytes() if source.is_file() else None

    def list_records(self, prefix: str) -> "list[str]":
        """Keys of the records under ``prefix`` (``ops/<kind>/``), oldest first."""
        folder = self.root / prefix
        if not prefix.startswith("ops/") or ".." in prefix or not folder.is_dir():
            return []
        keys = (path.relative_to(self.root).as_posix() for path in folder.rglob("*.json"))
        return sorted(key for key in keys if OPS_KEY_PATTERN.match(key))

    def delete_record(self, key: str):
        (self.root / ops_key(key)).unlink(missing_ok=True)

    def upload_url(self, user_id, media_id):
        return self._path(user_id, media_id).resolve().as_uri()


class S3Storage:
    """S3-compatible storage (Cloudflare R2, Backblaze B2, ...) with presigned URLs."""

    def __init__(self, endpoint_url, access_key_id, secret_access_key, bucket, region="auto"):
        import boto3
        from botocore.config import Config

        self.bucket = bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name=region,
            config=Config(signature_version="s3v4", retries={"max_attempts": 3}),
        )

    def put_file(self, user_id, media_id, source: Path):
        key = object_key(user_id, media_id)
        reject_connector_token(key)
        self.client.upload_file(
            str(source),
            self.bucket,
            key,
            ExtraArgs={"ContentType": content_type_for(media_id)},
        )

    def get_file(self, user_id, media_id, target: Path):
        from botocore.exceptions import ClientError

        try:
            self.client.download_file(self.bucket, object_key(user_id, media_id), str(target))
        except ClientError as exc:
            raise StorageError(f"Media '{media_id}' was not found.") from exc

    def delete(self, user_id, media_id):
        self.client.delete_object(Bucket=self.bucket, Key=object_key(user_id, media_id))

    def list(self, user_id) -> list[StoredObject]:
        items = []
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=f"users/{user_id}/media/"):
            for obj in page.get("Contents", []):
                media_id = obj["Key"].rsplit("/", 1)[1]
                if MEDIA_ID_PATTERN.match(media_id):
                    items.append(
                        StoredObject(
                            media_id,
                            media_kind(media_id),
                            obj["Size"],
                            obj["LastModified"].isoformat(),
                        )
                    )
        return sorted(items, key=lambda item: item.modified_at, reverse=True)

    def put_private(self, key: str, data: bytes):
        key = validate_private_key(key)
        reject_connector_token(key)
        # Body is ciphertext or a small session record. It is not logged.
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data)

    def get_private(self, key: str) -> bytes | None:
        from botocore.exceptions import ClientError

        try:
            return self.client.get_object(Bucket=self.bucket, Key=validate_private_key(key))["Body"].read()
        except ClientError:
            return None

    def delete_private(self, key: str):
        self.client.delete_object(Bucket=self.bucket, Key=validate_private_key(key))

    def list_user_ids(self) -> "list[str]":
        found = set()
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix="users/"):
            for obj in page.get("Contents", []):
                parts = obj["Key"].split("/")
                if len(parts) >= 2 and USER_ID_PATTERN.match(parts[1]):
                    found.add(parts[1])
        return sorted(found)

    def delete_user(self, user_id: str) -> int:
        """Delete everything stored under ``users/<user_id>/``. Returns how many objects."""
        prefix = _user_prefix(user_id) + "/"
        deleted = 0
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if key.startswith(prefix) and ".." not in key:
                    self.client.delete_object(Bucket=self.bucket, Key=key)
                    deleted += 1
        return deleted

    def download_url(self, user_id, media_id):
        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": object_key(user_id, media_id)},
            ExpiresIn=DOWNLOAD_URL_TTL_SECONDS,
        )

    def put_record(self, key: str, data: bytes):
        self.client.put_object(Bucket=self.bucket, Key=ops_key(key), Body=data, ContentType="application/json")

    def get_record(self, key: str) -> bytes | None:
        from botocore.exceptions import ClientError

        try:
            return self.client.get_object(Bucket=self.bucket, Key=ops_key(key))["Body"].read()
        except ClientError:
            return None

    def list_records(self, prefix: str) -> "list[str]":
        """Keys of the records under ``prefix`` (``ops/<kind>/``), oldest first."""
        if not prefix.startswith("ops/"):
            return []
        keys = []
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            keys.extend(obj["Key"] for obj in page.get("Contents", []) if OPS_KEY_PATTERN.match(obj["Key"]))
        return sorted(keys)

    def delete_record(self, key: str):
        self.client.delete_object(Bucket=self.bucket, Key=ops_key(key))

    def upload_url(self, user_id, media_id):
        return self.client.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": self.bucket,
                "Key": object_key(user_id, media_id),
                "ContentType": content_type_for(media_id),
            },
            ExpiresIn=UPLOAD_URL_TTL_SECONDS,
        )


def storage_from_env():
    """S3-compatible storage when configured, otherwise a local directory.

    Generic S3 (e.g. Backblaze B2): S3_ENDPOINT_URL, S3_ACCESS_KEY_ID,
    S3_SECRET_ACCESS_KEY, S3_BUCKET, optional S3_REGION.
    Cloudflare R2 shorthand: R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET.
    """
    s3_vars = ("S3_ENDPOINT_URL", "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY", "S3_BUCKET")
    if all(os.environ.get(name) for name in s3_vars):
        endpoint = os.environ["S3_ENDPOINT_URL"]
        region = os.environ.get("S3_REGION") or _region_from_endpoint(endpoint)
        return S3Storage(*(os.environ[name] for name in s3_vars), region=region)
    r2_vars = ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET")
    if all(os.environ.get(name) for name in r2_vars):
        account_id, *rest = (os.environ[name] for name in r2_vars)
        return S3Storage(f"https://{account_id}.r2.cloudflarestorage.com", *rest)
    if os.environ.get("RENDER"):
        raise StorageError("File storage is not configured on this deployment.")
    return LocalStorage(Path(os.environ.get("STUDIO_LOCAL_STORAGE", ".studio-storage")))


def _region_from_endpoint(endpoint: str) -> str:
    """Backblaze-style endpoints embed the region: https://s3.<region>.backblazeb2.com."""
    match = re.match(r"^https://s3\.([a-z0-9-]+)\.", endpoint)
    return match.group(1) if match else "auto"
