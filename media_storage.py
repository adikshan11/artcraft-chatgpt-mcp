"""Per-user media storage for the hosted ArtCraft Studio service.

Every object lives under ``users/<user-id>/media/<media-id>`` so one signed-in
user can never name another user's files. Production uses Cloudflare R2 through
its S3-compatible API; tests and local development use a plain directory.
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

MEDIA_ID_PATTERN = re.compile(r"^[0-9a-f]{32}\.[a-z0-9]{2,5}$")
USER_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
DOWNLOAD_URL_TTL_SECONDS = 3600
UPLOAD_URL_TTL_SECONDS = 900

ALLOWED_EXTENSIONS = {
    "image": {"png", "jpg", "jpeg", "webp", "gif", "bmp", "tiff"},
    "video": {"mp4", "mov", "webm", "mkv", "m4v"},
    "audio": {"mp3", "wav", "m4a", "aac", "ogg", "flac"},
    "pdf": {"pdf"},
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


def object_key(user_id: str, media_id: str) -> str:
    if not USER_ID_PATTERN.match(user_id):
        raise StorageError("Invalid user id.")
    return f"users/{user_id}/media/{validate_media_id(media_id)}"


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

    def download_url(self, user_id, media_id):
        return self._path(user_id, media_id).resolve().as_uri()

    def upload_url(self, user_id, media_id):
        return self._path(user_id, media_id).resolve().as_uri()


class R2Storage:
    """Cloudflare R2 storage using the S3-compatible API and presigned URLs."""

    def __init__(self, account_id, access_key_id, secret_access_key, bucket):
        import boto3
        from botocore.config import Config

        self.bucket = bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name="auto",
            config=Config(signature_version="s3v4", retries={"max_attempts": 3}),
        )

    def put_file(self, user_id, media_id, source: Path):
        self.client.upload_file(
            str(source),
            self.bucket,
            object_key(user_id, media_id),
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

    def download_url(self, user_id, media_id):
        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": object_key(user_id, media_id)},
            ExpiresIn=DOWNLOAD_URL_TTL_SECONDS,
        )

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
    """R2 when its credentials are configured, otherwise a local directory."""
    r2_vars = ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET")
    if all(os.environ.get(name) for name in r2_vars):
        return R2Storage(*(os.environ[name] for name in r2_vars))
    if os.environ.get("RENDER"):
        raise StorageError("R2 storage is not configured on this deployment.")
    return LocalStorage(Path(os.environ.get("STUDIO_LOCAL_STORAGE", ".studio-storage")))
