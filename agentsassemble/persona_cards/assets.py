"""Bounded asset extraction and persistence for persona imports."""

from __future__ import annotations

import base64
from pathlib import Path
import re
import shutil
import zipfile
from typing import Any

from agentsassemble.persona_cards.models import PersonaAsset, PersonaCard, PersonaImportReport
from agentsassemble.persona_cards.storage import save_persona_card
from agentsassemble.persona_cards.values import _text


DEFAULT_PERSONA_ROOT = "personas"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_CARD_TEXT_CHUNK_BYTES = 5 * 1024 * 1024
MAX_DATA_URI_BYTES = 50 * 1024 * 1024
MAX_CHARX_TOTAL_ASSET_BYTES = 60 * 1024 * 1024
MAX_CHARX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_CHARX_ENTRY_COUNT = 512
MAX_CHARX_TOTAL_UNCOMPRESSED_BYTES = 80 * 1024 * 1024
MAX_CHARX_COMPRESSION_RATIO = 200
MAX_CHARX_CARD_BYTES = 5 * 1024 * 1024
MAX_CHARX_MODULE_BYTES = 10 * 1024 * 1024


def validate_charx_archive(path: Path, archive: zipfile.ZipFile) -> None:
    if Path(path).stat().st_size > MAX_CHARX_ARCHIVE_BYTES:
        raise ValueError("CHARX archive is too large.")
    entries = archive.infolist()
    if len(entries) > MAX_CHARX_ENTRY_COUNT:
        raise ValueError("CHARX archive contains too many entries.")

    names: set[str] = set()
    total_uncompressed = 0
    for info in entries:
        name = info.filename
        normalized_name = name[:-1] if name.endswith("/") else name
        if not safe_embedded_path(normalized_name):
            raise ValueError("CHARX archive contains an unsafe entry path.")
        if name in names:
            raise ValueError("CHARX archive contains duplicate entries.")
        names.add(name)
        if info.flag_bits & 0x1:
            raise ValueError("Encrypted CHARX archive entries are not supported.")
        total_uncompressed += max(0, info.file_size)
        if total_uncompressed > MAX_CHARX_TOTAL_UNCOMPRESSED_BYTES:
            raise ValueError("CHARX archive expands beyond the safe size limit.")
        if info.file_size and (
            info.compress_size <= 0
            or info.file_size > info.compress_size * MAX_CHARX_COMPRESSION_RATIO
        ):
            raise ValueError("CHARX archive entry exceeds the safe compression ratio.")

    if "card.json" not in names:
        raise ValueError("CHARX file must contain card.json at the root.")


def read_charx_member(
    archive: zipfile.ZipFile,
    name: str,
    *,
    max_bytes: int,
) -> bytes:
    try:
        info = archive.getinfo(name)
    except KeyError as error:
        raise ValueError(f"CHARX archive does not contain {name}.") from error
    if info.file_size > max_bytes:
        raise ValueError(f"CHARX {name} exceeds the safe size limit.")
    with archive.open(info, "r") as source:
        payload = source.read(max_bytes + 1)
    if len(payload) > max_bytes or len(payload) != info.file_size:
        raise ValueError(f"CHARX {name} exceeds the safe size limit.")
    return payload


def charx_embedded_assets(
    archive: zipfile.ZipFile,
    referenced_paths: set[str],
) -> dict[str, bytes]:
    assets: dict[str, bytes] = {}
    total = 0
    for info in archive.infolist():
        name = info.filename
        if name in {"card.json", "module.risum"} or name.endswith("/"):
            continue
        safe_name = safe_embedded_path(name)
        if not safe_name or safe_name not in referenced_paths:
            continue
        if info.file_size > MAX_DATA_URI_BYTES:
            continue
        if total + info.file_size > MAX_CHARX_TOTAL_ASSET_BYTES:
            continue
        total += info.file_size
        assets[safe_name] = read_charx_member(
            archive,
            name,
            max_bytes=MAX_DATA_URI_BYTES,
        )
    return assets


def referenced_charx_asset_paths(asset_specs: list[dict[str, Any]]) -> set[str]:
    paths: set[str] = set()
    for asset in asset_specs:
        uri = _text(asset.get("uri"))
        if not uri.startswith("embeded://"):
            continue
        path = safe_embedded_path(uri.removeprefix("embeded://"))
        if path:
            paths.add(path)
    return paths


def write_imported_card(
    card: PersonaCard,
    *,
    source_path: Path,
    output_root: Path,
    asset_specs: list[dict[str, Any]],
    import_assets: dict[str, bytes],
    source_bytes: bytes | None,
    preserve_source: bool,
) -> PersonaImportReport:
    persona_dir = Path(output_root) / DEFAULT_PERSONA_ROOT / card.id
    assets_dir = persona_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    assets: list[PersonaAsset] = []
    ignored_features = dict(card.ignored_features)
    ignored_payloads = dict(card.ignored_payloads)
    for asset_spec in asset_specs:
        payload, ignored_key = _asset_payload_for_spec(asset_spec, import_assets, source_bytes)
        if ignored_key:
            ignored_features[ignored_key] = ignored_features.get(ignored_key, 0) + 1
            ignored_payloads.setdefault(ignored_key, []).append(_safe_ignored_asset_payload(asset_spec))
        if payload is None:
            continue
        asset_path, mime_type = write_asset_payload(assets_dir, len(assets), payload)
        assets.append(
            PersonaAsset(
                path=str(asset_path.relative_to(persona_dir)),
                mime_type=mime_type,
                source_index=len(assets),
                metadata=_safe_asset_metadata(asset_spec),
            )
        )
    card = PersonaCard.from_dict(
        {
            **card.to_dict(),
            "assets": [asset.to_dict() for asset in assets],
            "ignored_features": ignored_features,
            "ignored_payloads": ignored_payloads,
        }
    )
    card_path = persona_dir / "card.json"
    save_persona_card(card_path, card)
    if preserve_source:
        suffix = source_path.suffix or ".card"
        shutil.copyfile(source_path, persona_dir / f"source{suffix}")
    return PersonaImportReport(
        card=card,
        card_path=card_path,
        asset_count=len(assets),
        source_path=str(source_path),
    )


def write_asset_payload(assets_dir: Path, index: int, payload: bytes) -> tuple[Path, str]:
    extension, mime_type = _asset_extension(payload)
    path = assets_dir / f"asset-{index:03d}{extension}"
    path.write_bytes(payload)
    return path, mime_type


def safe_embedded_path(value: str) -> str:
    raw = str(value or "").replace("\\", "/")
    if raw.startswith("/") or raw.endswith("/") or "//" in raw:
        return ""
    parts = raw.split("/")
    if not parts or any(not part or part in {".", ".."} for part in parts):
        return ""
    if ":" in parts[0]:
        return ""
    return "/".join(parts)


def _asset_payload_for_spec(
    asset_spec: dict[str, Any],
    import_assets: dict[str, bytes],
    source_bytes: bytes | None,
) -> tuple[bytes | None, str]:
    uri = _text(asset_spec.get("uri"))
    if uri == "ccdefault:":
        return source_bytes, "" if source_bytes is not None else "missing_asset"
    if uri.startswith("__asset:"):
        key = uri.removeprefix("__asset:")
        payload = import_assets.get(key)
        return payload, "" if payload is not None else "missing_asset"
    if uri.startswith("embeded://"):
        key = safe_embedded_path(uri.removeprefix("embeded://"))
        if not key:
            return None, "unsafe_asset_uri"
        payload = import_assets.get(key)
        return payload, "" if payload is not None else "missing_asset"
    if uri.startswith("data:"):
        payload = _data_uri_payload(uri)
        return payload, "" if payload is not None else "oversized_or_invalid_data_uri"
    if uri.startswith("http://") or uri.startswith("https://"):
        return None, "remote_asset_uri"
    if uri.startswith("file:") or re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", uri):
        return None, "unsupported_asset_uri"
    return None, "unsupported_asset_uri" if uri else "missing_asset"


def _data_uri_payload(uri: str) -> bytes | None:
    if "," not in uri:
        return None
    _header, encoded = uri.split(",", 1)
    if len(encoded) > MAX_DATA_URI_BYTES * 2:
        return None
    try:
        payload = base64.b64decode(encoded, validate=True)
    except ValueError:
        return None
    if len(payload) > MAX_DATA_URI_BYTES:
        return None
    return payload


def _safe_asset_metadata(asset_spec: dict[str, Any]) -> dict[str, object]:
    metadata: dict[str, object] = {}
    for key in ("type", "name", "ext", "uri"):
        value = _text(asset_spec.get(key))
        if not value:
            continue
        if key == "uri" and (
            value.startswith("http://")
            or value.startswith("https://")
            or value.startswith("file:")
        ):
            continue
        metadata[key] = value
    return metadata


def _safe_ignored_asset_payload(asset_spec: dict[str, Any]) -> dict[str, object]:
    return {
        "type": _text(asset_spec.get("type")),
        "name": _text(asset_spec.get("name")),
        "ext": _text(asset_spec.get("ext")),
        "uri_kind": _asset_uri_kind(_text(asset_spec.get("uri"))),
    }


def _asset_uri_kind(uri: str) -> str:
    if uri.startswith("http://") or uri.startswith("https://"):
        return "remote"
    if uri.startswith("file:"):
        return "file"
    if uri.startswith("data:"):
        return "data"
    if uri.startswith("embeded://"):
        return "embeded"
    if uri.startswith("__asset:"):
        return "__asset"
    return uri or "missing"


def _asset_extension(payload: bytes) -> tuple[str, str]:
    if payload.startswith(PNG_SIGNATURE):
        return ".png", "image/png"
    if payload.startswith(b"\xff\xd8\xff"):
        return ".jpg", "image/jpeg"
    if payload.startswith(b"GIF87a") or payload.startswith(b"GIF89a"):
        return ".gif", "image/gif"
    if payload[:4] == b"RIFF" and payload[8:12] == b"WEBP":
        return ".webp", "image/webp"
    return ".bin", "application/octet-stream"
