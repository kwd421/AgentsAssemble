from __future__ import annotations

import base64
import binascii
import json
import mimetypes
import re
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

from agentsassemble.room.text import clean_room_text

MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
MAX_ATTACHMENTS_PER_EVENT = 8
ATTACHMENT_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
# Only safe raster types count as renderable images. svg/html and other active
# types must never be classified as is_image so the UI does not preview them and
# the server does not serve them inline.
INLINE_SAFE_IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
PUBLIC_ATTACHMENT_PURPOSES = {"profile_avatar", "room_appearance"}


class AttachmentError(ValueError):
    pass


@dataclass(frozen=True)
class FileAttachmentStore:
    """Filesystem attachment boundary for one GUI application root."""

    output_root: Path

    @property
    def root(self) -> Path:
        return attachment_root(self.output_root)

    def store(self, payload: dict[str, object]) -> dict[str, object]:
        return store_uploaded_attachment(self.output_root, payload)

    def normalize_references(
        self,
        value: object,
        *,
        room_id: str = "",
    ) -> list[dict[str, object]]:
        return normalize_attachment_references(
            self.output_root,
            value,
            room_id=room_id,
        )

    def read_file(self, attachment_id: str) -> tuple[dict[str, object], Path]:
        return read_attachment_file(self.output_root, attachment_id)

    def read_metadata(self, attachment_id: str) -> dict[str, object]:
        return read_attachment_metadata(self.output_root, attachment_id)

    def delete(self, attachment_id: str) -> bool:
        return delete_attachment(self.output_root, attachment_id)


def store_uploaded_attachment(output_root: Path, payload: dict[str, object]) -> dict[str, object]:
    filename = sanitize_attachment_filename(payload.get("filename"))
    content_type = normalize_content_type(payload.get("content_type"), filename)
    room_id = clean_room_text(
        payload.get("room_id") or payload.get("meeting_id"),
        limit=128,
    )
    raw = decode_attachment_data(payload.get("data_base64"))
    if len(raw) > MAX_ATTACHMENT_BYTES:
        raise AttachmentError("Attachment is too large")
    attachment_id = uuid4().hex
    directory = attachment_root(output_root) / attachment_id
    directory.mkdir(parents=True, exist_ok=False)
    file_path = directory / filename
    file_path.write_bytes(raw)
    metadata = {
        "id": attachment_id,
        "filename": filename,
        "storage_filename": filename,
        "content_type": content_type,
        "size": len(raw),
        "is_image": content_type in INLINE_SAFE_IMAGE_TYPES,
        "created_at": datetime.now(UTC).isoformat(),
        "room_id": room_id,
        "purpose": normalize_attachment_purpose(payload.get("purpose")),
    }
    (directory / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return public_attachment_metadata(metadata)


def normalize_attachment_references(
    output_root: Path,
    value: object,
    *,
    room_id: str = "",
) -> list[dict[str, object]]:
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise AttachmentError("attachments must be a list")
    attachments: list[dict[str, object]] = []
    seen: set[str] = set()
    for item in value[:MAX_ATTACHMENTS_PER_EVENT]:
        if not isinstance(item, dict):
            raise AttachmentError("attachment references must be objects")
        attachment_id = normalize_attachment_id(item.get("id"))
        if attachment_id in seen:
            continue
        stored_metadata = read_attachment_metadata(output_root, attachment_id)
        if room_id and clean_room_text(stored_metadata.get("room_id"), limit=128) != room_id:
            raise AttachmentError("Attachment is not part of this room")
        metadata, _file_path = read_attachment_file(output_root, attachment_id)
        attachments.append(metadata)
        seen.add(attachment_id)
    if len(value) > MAX_ATTACHMENTS_PER_EVENT:
        raise AttachmentError(f"at most {MAX_ATTACHMENTS_PER_EVENT} attachments are allowed")
    return attachments


def read_attachment_file(output_root: Path, attachment_id: str) -> tuple[dict[str, object], Path]:
    normalized_id = normalize_attachment_id(attachment_id)
    metadata = read_attachment_metadata(output_root, normalized_id)
    storage_filename = sanitize_attachment_filename(metadata.get("storage_filename") or metadata.get("filename"))
    base = (attachment_root(output_root) / normalized_id).resolve()
    candidate = (base / storage_filename).resolve()
    try:
        candidate.relative_to(base)
    except ValueError as error:
        raise AttachmentError("Attachment path is invalid") from error
    if not candidate.exists() or not candidate.is_file():
        raise AttachmentError("Attachment not found")
    return public_attachment_metadata(metadata), candidate


def read_attachment_metadata(output_root: Path, attachment_id: str) -> dict[str, object]:
    normalized_id = normalize_attachment_id(attachment_id)
    path = attachment_root(output_root) / normalized_id / "metadata.json"
    try:
        metadata = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise AttachmentError("Attachment not found") from error
    if not isinstance(metadata, dict):
        raise AttachmentError("Attachment metadata is invalid")
    metadata["id"] = normalized_id
    return metadata


def delete_attachment(output_root: Path, attachment_id: str) -> bool:
    """Delete one exact attachment directory after validating its opaque id."""

    normalized_id = normalize_attachment_id(attachment_id)
    directory = attachment_root(output_root) / normalized_id
    if not directory.exists():
        return False
    if not directory.is_dir() or directory.is_symlink():
        raise AttachmentError("Attachment storage is invalid")
    shutil.rmtree(directory)
    return True


def public_attachment_metadata(metadata: dict[str, object]) -> dict[str, object]:
    attachment_id = normalize_attachment_id(metadata.get("id"))
    filename = sanitize_attachment_filename(metadata.get("filename"))
    content_type = normalize_content_type(metadata.get("content_type"), filename)
    size = normalize_size(metadata.get("size"))
    is_image = content_type in INLINE_SAFE_IMAGE_TYPES
    return {
        "id": attachment_id,
        "filename": filename,
        "content_type": content_type,
        "size": size,
        "is_image": is_image,
        "url": f"/api/attachments/{attachment_id}?view=1",
        "download_url": f"/api/attachments/{attachment_id}?download=1",
    }


def attachment_content_disposition(filename: str, *, inline: bool) -> str:
    disposition = "inline" if inline else "attachment"
    fallback = re.sub(r"[^A-Za-z0-9._-]+", "_", filename).strip("._") or "attachment"
    return f"{disposition}; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename)}"


def attachment_root(output_root: Path) -> Path:
    return output_root / "attachments"


def delete_room_attachments(output_root: Path, room_id: str) -> int:
    """Remove uploaded files owned by one room.

    Invalid or unscoped attachment directories are preserved because their
    ownership cannot be established safely.
    """
    scoped_room_id = clean_room_text(room_id, limit=128)
    if not scoped_room_id:
        return 0
    root = attachment_root(output_root)
    if not root.is_dir():
        return 0
    removed = 0
    for directory in root.iterdir():
        if not directory.is_dir() or directory.is_symlink():
            continue
        try:
            metadata = json.loads(
                (directory / "metadata.json").read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(metadata, dict):
            continue
        owner_room_id = clean_room_text(metadata.get("room_id"), limit=128)
        if owner_room_id != scoped_room_id:
            continue
        shutil.rmtree(directory)
        removed += 1
    return removed


def normalize_attachment_id(value: object) -> str:
    attachment_id = str(value or "").strip()
    if not ATTACHMENT_ID_PATTERN.fullmatch(attachment_id):
        raise AttachmentError("Attachment id is invalid")
    return attachment_id


def sanitize_attachment_filename(value: object) -> str:
    raw = str(value or "attachment.bin").replace("\\", "/")
    name = Path(raw).name
    name = "".join(ch for ch in name if ch >= " " and ch not in {"/", "\\", "\x7f"}).strip()
    if name in {"", ".", ".."}:
        return "attachment.bin"
    return name[:120]


def normalize_content_type(value: object, filename: str) -> str:
    content_type = str(value or "").split(";", 1)[0].strip().lower()
    if not re.fullmatch(r"[a-z0-9.+-]+/[a-z0-9.+-]+", content_type):
        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    return content_type


def decode_attachment_data(value: object) -> bytes:
    if not isinstance(value, str) or not value.strip():
        raise AttachmentError("data_base64 is required")
    data = value.strip()
    if "," in data and data[:64].lower().startswith("data:"):
        data = data.split(",", 1)[1]
    try:
        return base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError) as error:
        raise AttachmentError("data_base64 is invalid") from error


def normalize_size(value: object) -> int:
    try:
        size = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, min(size, MAX_ATTACHMENT_BYTES))


def normalize_attachment_purpose(value: object) -> str:
    purpose = clean_room_text(value, limit=32)
    if purpose in PUBLIC_ATTACHMENT_PURPOSES:
        return purpose
    return "room_attachment"
