from __future__ import annotations

import json
import os
import re
import shutil
import base64
import hashlib
import struct
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


RISU_MODULE_MAGIC = 111
RISU_MODULE_VERSION = 0
RISU_ASSET_MARKER = 1
RISU_EOF_MARKER = 0
DEFAULT_PERSONA_ROOT = "personas"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_CARD_TEXT_CHUNK_BYTES = 5 * 1024 * 1024
MAX_DATA_URI_BYTES = 50 * 1024 * 1024
MAX_CHARX_TOTAL_ASSET_BYTES = 60 * 1024 * 1024


@dataclass(frozen=True)
class RisuModulePayload:
    module: dict[str, Any]
    asset_payloads: list[bytes] = field(default_factory=list)
    source_path: str = ""


@dataclass(frozen=True)
class PersonaLoreEntry:
    key: str = ""
    content: str = ""
    secondkey: str = ""
    comment: str = ""
    always_active: bool = False
    selective: bool = False
    use_regex: bool = False
    insert_order: int = 0
    position: str = ""
    role: str = ""
    enabled: bool = True
    case_sensitive: bool = False
    priority: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "content": self.content,
            "secondkey": self.secondkey,
            "comment": self.comment,
            "always_active": self.always_active,
            "selective": self.selective,
            "use_regex": self.use_regex,
            "insert_order": self.insert_order,
            "position": self.position,
            "role": self.role,
            "enabled": self.enabled,
            "case_sensitive": self.case_sensitive,
            "priority": self.priority,
            "extra": self.extra,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PersonaLoreEntry:
        keys = data.get("keys")
        secondary_keys = data.get("secondary_keys")
        extensions = data.get("extensions") if isinstance(data.get("extensions"), dict) else {}
        extra = data.get("extra") if isinstance(data.get("extra"), dict) else {}
        return cls(
            key=_text(data.get("key")) or _joined_strings(keys),
            content=_raw_text(data.get("content")),
            secondkey=_text(data.get("secondkey")) or _joined_strings(secondary_keys),
            comment=_text(data.get("comment")) or _text(data.get("name")),
            always_active=bool(data.get("always_active", data.get("alwaysActive", data.get("constant", False)))),
            selective=bool(data.get("selective", False)),
            use_regex=bool(data.get("use_regex", data.get("useRegex", data.get("use_regex", False)))),
            insert_order=_int(data.get("insert_order", data.get("insertorder", data.get("insertion_order", 0)))),
            position=_text(data.get("position")),
            role=_text(data.get("role")),
            enabled=bool(data.get("enabled", True)),
            case_sensitive=bool(data.get("case_sensitive", extensions.get("risu_case_sensitive", False))),
            priority=_int(data.get("priority", data.get("insert_order", data.get("insertorder", 0)))),
            extra=dict(extra) if extra else _lore_extra(data),
        )


@dataclass(frozen=True)
class PersonaAsset:
    path: str = ""
    mime_type: str = "application/octet-stream"
    source_index: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "mime_type": self.mime_type,
            "source_index": self.source_index,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PersonaAsset:
        metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
        return cls(
            path=_text(data.get("path")),
            mime_type=_text(data.get("mime_type")) or "application/octet-stream",
            source_index=_int(data.get("source_index")),
            metadata=dict(metadata),
        )


@dataclass(frozen=True)
class PersonaCard:
    id: str
    display_name: str
    description: str = ""
    system_prompt: str = ""
    personality: str = ""
    scenario: str = ""
    first_message: str = ""
    alternate_greetings: list[str] = field(default_factory=list)
    group_only_greetings: list[str] = field(default_factory=list)
    example_messages: str = ""
    post_history_instructions: str = ""
    creator_notes: str = ""
    talkativeness: float = 0.66
    active: bool = True
    lorebook: list[PersonaLoreEntry] = field(default_factory=list)
    lore_settings: dict[str, object] = field(default_factory=dict)
    assets: list[PersonaAsset] = field(default_factory=list)
    source: dict[str, object] = field(default_factory=dict)
    ignored_features: dict[str, int] = field(default_factory=dict)
    ignored_payloads: dict[str, object] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    extra: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "display_name": self.display_name,
            "description": self.description,
            "system_prompt": self.system_prompt,
            "personality": self.personality,
            "scenario": self.scenario,
            "first_message": self.first_message,
            "alternate_greetings": list(self.alternate_greetings),
            "group_only_greetings": list(self.group_only_greetings),
            "example_messages": self.example_messages,
            "post_history_instructions": self.post_history_instructions,
            "creator_notes": self.creator_notes,
            "talkativeness": self.talkativeness,
            "active": self.active,
            "lorebook": [entry.to_dict() for entry in self.lorebook],
            "lore_settings": self.lore_settings,
            "assets": [asset.to_dict() for asset in self.assets],
            "source": self.source,
            "ignored_features": self.ignored_features,
            "ignored_payloads": self.ignored_payloads,
            "tags": list(self.tags),
            "extra": self.extra,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PersonaCard:
        lorebook = data.get("lorebook") if isinstance(data.get("lorebook"), list) else []
        lore_settings = data.get("lore_settings") if isinstance(data.get("lore_settings"), dict) else {}
        assets = data.get("assets") if isinstance(data.get("assets"), list) else []
        source = data.get("source") if isinstance(data.get("source"), dict) else {}
        ignored_features = data.get("ignored_features") if isinstance(data.get("ignored_features"), dict) else {}
        ignored_payloads = data.get("ignored_payloads") if isinstance(data.get("ignored_payloads"), dict) else {}
        tags = data.get("tags") if isinstance(data.get("tags"), list) else []
        extra = data.get("extra") if isinstance(data.get("extra"), dict) else {}
        return cls(
            id=_text(data.get("id")) or "persona",
            display_name=_text(data.get("display_name")) or _text(data.get("name")) or "Persona",
            description=_text(data.get("description")),
            system_prompt=_text(data.get("system_prompt")),
            personality=_text(data.get("personality")),
            scenario=_text(data.get("scenario")),
            first_message=_text(data.get("first_message")),
            alternate_greetings=_string_list(data.get("alternate_greetings")),
            group_only_greetings=_string_list(data.get("group_only_greetings")),
            example_messages=_text(data.get("example_messages")),
            post_history_instructions=_text(data.get("post_history_instructions")),
            creator_notes=_text(data.get("creator_notes")),
            talkativeness=_float(data.get("talkativeness"), 0.66),
            active=bool(data.get("active", True)),
            lorebook=[PersonaLoreEntry.from_dict(entry) for entry in lorebook if isinstance(entry, dict)],
            lore_settings=dict(lore_settings),
            assets=[PersonaAsset.from_dict(asset) for asset in assets if isinstance(asset, dict)],
            source=dict(source),
            ignored_features={str(key): _int(value) for key, value in ignored_features.items()},
            ignored_payloads=dict(ignored_payloads),
            tags=[str(tag) for tag in tags if isinstance(tag, str)],
            extra=dict(extra),
        )

    def safe_summary(self) -> dict[str, object]:
        return {
            "id": self.id,
            "display_name": self.display_name,
            "description_length": len(self.description),
            "source": self.source,
            "lorebook_count": len(self.lorebook),
            "asset_count": len(self.assets),
            "ignored_features": self.ignored_features,
            "tags": list(self.tags),
            "content_lengths": {
                "system_prompt": len(self.system_prompt),
                "personality": len(self.personality),
                "scenario": len(self.scenario),
                "post_history_instructions": len(self.post_history_instructions),
            },
        }


@dataclass(frozen=True)
class PersonaImportReport:
    card: PersonaCard
    card_path: Path
    asset_count: int = 0
    source_path: str = ""

    @property
    def lorebook_count(self) -> int:
        return len(self.card.lorebook)

    def to_safe_dict(self) -> dict[str, object]:
        return {
            "persona": self.card.safe_summary(),
            "card_path": str(self.card_path),
            "source_path": self.source_path,
            "lorebook_count": self.lorebook_count,
            "asset_count": self.asset_count,
            "ignored_features": self.card.ignored_features,
        }


def read_risum_module(path: Path, *, rpack_map_path: Path | None = None) -> RisuModulePayload:
    data = Path(path).read_bytes()
    return read_risum_module_bytes(data, rpack_map_path=rpack_map_path, source_path=str(path))


def read_risum_module_bytes(
    data: bytes,
    *,
    rpack_map_path: Path | None = None,
    source_path: str = "",
) -> RisuModulePayload:
    if len(data) < 6:
        raise ValueError("Risu module file is too small.")
    if data[0] != RISU_MODULE_MAGIC:
        raise ValueError("Risu module magic byte is invalid.")
    if data[1] != RISU_MODULE_VERSION:
        raise ValueError(f"Unsupported Risu module version: {data[1]}")
    rpack_map = _load_rpack_map(rpack_map_path)
    offset = 2
    main_length = _read_uint32(data, offset)
    offset += 4
    main_encoded = _slice_record(data, offset, main_length, "main module payload")
    offset += main_length
    main_payload = json.loads(decode_rpack(main_encoded, rpack_map).decode("utf-8"))
    if not isinstance(main_payload, dict) or main_payload.get("type") != "risuModule":
        raise ValueError("Risu module payload must have type 'risuModule'.")
    module = main_payload.get("module")
    if not isinstance(module, dict):
        raise ValueError("Risu module payload must contain a module object.")

    assets: list[bytes] = []
    while offset < len(data):
        marker = data[offset]
        offset += 1
        if marker == RISU_EOF_MARKER:
            break
        if marker != RISU_ASSET_MARKER:
            raise ValueError(f"Unsupported Risu module record marker: {marker}")
        asset_length = _read_uint32(data, offset)
        offset += 4
        asset_encoded = _slice_record(data, offset, asset_length, "asset payload")
        offset += asset_length
        assets.append(decode_rpack(asset_encoded, rpack_map))

    return RisuModulePayload(module=dict(module), asset_payloads=assets, source_path=source_path)


def decode_rpack(data: bytes, map_data: bytes) -> bytes:
    if len(map_data) < 512:
        raise ValueError("Risu rpack map must contain at least 512 bytes.")
    decode_map = map_data[256:512]
    return bytes(decode_map[byte] for byte in data)


def persona_card_from_risu_module(module: dict[str, Any], *, source_name: str = "") -> PersonaCard:
    persona_id = _safe_persona_id(_text(module.get("id")) or _text(module.get("name")) or Path(source_name).stem)
    lorebook_data = module.get("lorebook") if isinstance(module.get("lorebook"), list) else []
    assets_data = module.get("assets") if isinstance(module.get("assets"), list) else []
    ignored_features, ignored_payloads = _ignored_risu_features(module, lorebook_data)
    display_name = _text(module.get("name")) or persona_id
    return PersonaCard(
        id=persona_id,
        display_name=display_name,
        description=_text(module.get("description")),
        system_prompt=_text(module.get("systemPrompt") or module.get("system_prompt")),
        personality=_text(module.get("personality")),
        scenario=_text(module.get("scenario")),
        first_message=_text(module.get("firstMessage") or module.get("first_message")),
        alternate_greetings=_string_list(module.get("alternateGreetings") or module.get("alternate_greetings")),
        group_only_greetings=_string_list(module.get("groupOnlyGreetings") or module.get("group_only_greetings")),
        example_messages=_text(module.get("exampleMessage") or module.get("example_messages")),
        post_history_instructions=_text(module.get("postHistoryInstructions") or module.get("post_history_instructions")),
        lorebook=[PersonaLoreEntry.from_dict(entry) for entry in lorebook_data if isinstance(entry, dict)],
        assets=[
            PersonaAsset(source_index=index, metadata=dict(asset) if isinstance(asset, dict) else {})
            for index, asset in enumerate(assets_data)
        ],
        source={
            "kind": "risu_module",
            "source_name": source_name,
            "imported_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        },
        ignored_features=ignored_features,
        ignored_payloads=ignored_payloads,
        extra={
            key: module[key]
            for key in ("namespace", "hideIcon", "backgroundEmbedding")
            if key in module
        },
    )


def persona_card_from_ccv3(card: dict[str, Any], *, source_name: str = "") -> PersonaCard:
    data = _ccv3_data(card)
    display_name = _text(data.get("name")) or Path(source_name).stem or "Persona"
    persona_id = _safe_persona_id(display_name)
    charbook = data.get("character_book") if isinstance(data.get("character_book"), dict) else {}
    lorebook = _lore_entries_from_character_book(charbook)
    ignored_features, ignored_payloads = _ignored_ccv3_features(data, lorebook)
    source = _ccv3_source(card, data, source_name=source_name)
    extensions = data.get("extensions") if isinstance(data.get("extensions"), dict) else {}
    return PersonaCard(
        id=persona_id,
        display_name=display_name,
        description=_text(data.get("description")),
        system_prompt=_text(data.get("system_prompt")),
        personality=_text(data.get("personality")),
        scenario=_text(data.get("scenario")),
        first_message=_text(data.get("first_mes")),
        alternate_greetings=_string_list(data.get("alternate_greetings")),
        group_only_greetings=_string_list(data.get("group_only_greetings")),
        example_messages=_text(data.get("mes_example")),
        post_history_instructions=_text(data.get("post_history_instructions")),
        creator_notes=_text(data.get("creator_notes")),
        lorebook=lorebook,
        lore_settings=_lore_settings_from_character_book(charbook),
        assets=[],
        source=source,
        ignored_features=ignored_features,
        ignored_payloads=ignored_payloads,
        tags=_string_list(data.get("tags")),
        extra={
            "creator": _text(data.get("creator")),
            "character_version": _text(data.get("character_version")),
            "nickname": _text(data.get("nickname")),
            "source": _string_list(data.get("source")),
            "creation_date": data.get("creation_date"),
            "modification_date": data.get("modification_date"),
            "extensions": {key: value for key, value in extensions.items() if key != "risuai"},
            "risuai_extensions": _unknown_risuai_extensions(data),
            "unknown_card_fields": _unknown_card_fields(card),
            "unknown_data_fields": _unknown_data_fields(data),
        },
    )


def import_ccv3_persona(
    path: Path,
    *,
    output_root: Path,
    preserve_source: bool = True,
) -> PersonaImportReport:
    card_payload, import_assets, source_bytes, container = _read_ccv3_card_source(Path(path))
    card = persona_card_from_ccv3(card_payload, source_name=Path(path).name)
    card = _card_with_source(card, {"container": container})
    return _write_imported_card(
        card,
        source_path=Path(path),
        output_root=output_root,
        asset_specs=_ccv3_asset_specs(card_payload),
        import_assets=import_assets,
        source_bytes=source_bytes,
        preserve_source=preserve_source,
    )


def import_charx_persona(
    path: Path,
    *,
    output_root: Path,
    preserve_source: bool = True,
) -> PersonaImportReport:
    with zipfile.ZipFile(path) as archive:
        try:
            card_payload = json.loads(archive.read("card.json").decode("utf-8"))
        except KeyError as error:
            raise ValueError("CHARX file must contain card.json at the root.") from error
        if not isinstance(card_payload, dict):
            raise ValueError("CHARX card.json must contain a JSON object.")
        asset_specs = _ccv3_asset_specs(card_payload)
        referenced_paths = _referenced_charx_asset_paths(asset_specs)
        import_assets = _charx_embedded_assets(archive, referenced_paths)
        module_payload = _charx_module_payload(archive)
        module_present = "module.risum" in archive.namelist()
    module_ignored_features: dict[str, int] = {}
    module_ignored_payloads: dict[str, object] = {}
    if module_payload is not None and isinstance(card_payload.get("data"), dict):
        data = dict(card_payload["data"])
        module_lore = module_payload.module.get("lorebook")
        module_ignored_features, module_ignored_payloads = _ignored_risu_features(
            module_payload.module,
            module_lore if isinstance(module_lore, list) else [],
        )
        if isinstance(module_lore, list):
            data["character_book"] = _character_book_from_risu_lorebook(module_lore)
        card_payload = {**card_payload, "data": data}
    card = persona_card_from_ccv3(card_payload, source_name=Path(path).name)
    if module_present and module_payload is None:
        card = PersonaCard.from_dict(
            {
                **card.to_dict(),
                "ignored_features": _merge_ignored_counts(
                    card.ignored_features,
                    {"embedded_module_unreadable": 1},
                ),
                "ignored_payloads": {
                    **card.ignored_payloads,
                    "embedded_module_unreadable": {
                        "reason": "unreadable",
                    },
                },
            }
        )
    if module_ignored_features or module_ignored_payloads:
        card = PersonaCard.from_dict(
            {
                **card.to_dict(),
                "ignored_features": _merge_ignored_counts(card.ignored_features, module_ignored_features),
                "ignored_payloads": {
                    **card.ignored_payloads,
                    "embedded_module": {
                        "features": module_ignored_features,
                        "payloads": module_ignored_payloads,
                    },
                },
            }
        )
    card = _card_with_source(card, {"container": "charx"})
    return _write_imported_card(
        card,
        source_path=Path(path),
        output_root=output_root,
        asset_specs=asset_specs,
        import_assets=import_assets,
        source_bytes=None,
        preserve_source=preserve_source,
    )


def import_risum_persona(
    path: Path,
    *,
    output_root: Path,
    rpack_map_path: Path | None = None,
    preserve_source: bool = True,
) -> PersonaImportReport:
    payload = read_risum_module(Path(path), rpack_map_path=rpack_map_path)
    card = persona_card_from_risu_module(payload.module, source_name=Path(path).name)
    persona_dir = Path(output_root) / DEFAULT_PERSONA_ROOT / card.id
    assets_dir = persona_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    assets = list(card.assets)
    for index, asset_payload in enumerate(payload.asset_payloads):
        asset_path, mime_type = _write_asset_payload(assets_dir, index, asset_payload)
        metadata = assets[index].metadata if index < len(assets) else {}
        asset = PersonaAsset(
            path=str(asset_path.relative_to(persona_dir)),
            mime_type=mime_type,
            source_index=index,
            metadata=metadata,
        )
        if index < len(assets):
            assets[index] = asset
        else:
            assets.append(asset)

    card = PersonaCard.from_dict({**card.to_dict(), "assets": [asset.to_dict() for asset in assets]})
    card_path = persona_dir / "card.json"
    save_persona_card(card_path, card)
    if preserve_source:
        shutil.copyfile(Path(path), persona_dir / "source.risum")
    return PersonaImportReport(card=card, card_path=card_path, asset_count=len(payload.asset_payloads), source_path=str(path))


def _read_ccv3_card_source(path: Path) -> tuple[dict[str, Any], dict[str, bytes], bytes | None, str]:
    suffix = path.suffix.lower()
    if suffix == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("CCv3 JSON file must contain a JSON object.")
        return payload, {}, None, "json"
    if suffix in {".png", ".apng"}:
        data = path.read_bytes()
        card_payload, assets = _read_ccv3_png_payload(data)
        return card_payload, assets, data, "png"
    raise ValueError("CCv3 import supports .json, .png, and .apng files.")


def _read_ccv3_png_payload(data: bytes) -> tuple[dict[str, Any], dict[str, bytes]]:
    if not data.startswith(PNG_SIGNATURE):
        raise ValueError("PNG character card has an invalid signature.")
    offset = len(PNG_SIGNATURE)
    text_chunks: dict[str, str] = {}
    assets: dict[str, bytes] = {}
    while offset + 12 <= len(data):
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        offset += 4
        kind = data[offset : offset + 4]
        offset += 4
        payload = data[offset : offset + length]
        offset += length + 4
        if length > MAX_CARD_TEXT_CHUNK_BYTES and kind == b"tEXt":
            continue
        if kind != b"tEXt":
            if kind == b"IEND":
                break
            continue
        try:
            key_bytes, value_bytes = payload.split(b"\x00", 1)
        except ValueError:
            continue
        key = key_bytes.decode("latin-1")
        value = value_bytes.decode("latin-1")
        if key.startswith("chara-ext-asset_"):
            asset_key = key.replace("chara-ext-asset_:", "", 1).replace("chara-ext-asset_", "", 1)
            if asset_key:
                assets[asset_key] = base64.b64decode(value)
            continue
        if key in {"ccv3", "chara"}:
            text_chunks[key] = value
    selected = text_chunks.get("ccv3") or text_chunks.get("chara")
    if not selected:
        raise ValueError("PNG character card must contain a ccv3 or chara text chunk.")
    payload = json.loads(base64.b64decode(selected).decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("PNG character card payload must contain a JSON object.")
    return payload, assets


def _charx_embedded_assets(archive: zipfile.ZipFile, referenced_paths: set[str]) -> dict[str, bytes]:
    assets: dict[str, bytes] = {}
    total = 0
    for info in archive.infolist():
        name = info.filename
        if name in {"card.json", "module.risum"} or name.endswith("/"):
            continue
        safe_name = _safe_embedded_path(name)
        if not safe_name or safe_name not in referenced_paths:
            continue
        if info.file_size > MAX_DATA_URI_BYTES:
            continue
        if total + info.file_size > MAX_CHARX_TOTAL_ASSET_BYTES:
            continue
        total += info.file_size
        assets[safe_name] = archive.read(info)
    return assets


def _charx_module_payload(archive: zipfile.ZipFile) -> RisuModulePayload | None:
    if "module.risum" not in archive.namelist():
        return None
    try:
        return read_risum_module_bytes(archive.read("module.risum"), source_path="module.risum")
    except Exception:
        return None


def _ccv3_data(card: dict[str, Any]) -> dict[str, Any]:
    if card.get("spec") != "chara_card_v3":
        raise ValueError("Character card spec must be 'chara_card_v3'.")
    data = card.get("data")
    if not isinstance(data, dict):
        raise ValueError("Character card data must be a JSON object.")
    return data


def _ccv3_source(card: dict[str, Any], data: dict[str, Any], *, source_name: str) -> dict[str, object]:
    extensions = data.get("extensions") if isinstance(data.get("extensions"), dict) else {}
    risuai = extensions.get("risuai") if isinstance(extensions.get("risuai"), dict) else {}
    return {
        "kind": "ccv3",
        "source_name": source_name,
        "spec_version": _text(card.get("spec_version")),
        "card_source": _string_list(data.get("source")),
        "creation_date": data.get("creation_date"),
        "modification_date": data.get("modification_date"),
        "realm_import_id": _text(risuai.get("risuRealmImportId")),
        "card_hash": "sha256:" + _json_hash(card),
        "imported_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }


def _card_with_source(card: PersonaCard, source_overlay: dict[str, object]) -> PersonaCard:
    return PersonaCard.from_dict({**card.to_dict(), "source": {**card.source, **source_overlay}})


def _lore_entries_from_character_book(charbook: dict[str, Any]) -> list[PersonaLoreEntry]:
    entries = charbook.get("entries") if isinstance(charbook.get("entries"), list) else []
    return [PersonaLoreEntry.from_dict(entry) for entry in entries if isinstance(entry, dict)]


def _lore_settings_from_character_book(charbook: dict[str, Any]) -> dict[str, object]:
    if not charbook:
        return {}
    extensions = charbook.get("extensions") if isinstance(charbook.get("extensions"), dict) else {}
    settings: dict[str, object] = {}
    for source_key, target_key in (
        ("scan_depth", "scan_depth"),
        ("token_budget", "token_budget"),
        ("recursive_scanning", "recursive_scanning"),
    ):
        if source_key in charbook:
            settings[target_key] = charbook[source_key]
    if "risu_fullWordMatching" in extensions:
        settings["full_word_matching"] = bool(extensions["risu_fullWordMatching"])
    return settings


def _character_book_from_risu_lorebook(lorebook: list[Any]) -> dict[str, object]:
    entries: list[dict[str, object]] = []
    for item in lorebook:
        if not isinstance(item, dict):
            continue
        entries.append(
            {
                "keys": _keywords(_text(item.get("key"))),
                "secondary_keys": _keywords(_text(item.get("secondkey"))),
                "content": _raw_text(item.get("content")),
                "enabled": bool(item.get("enabled", True)),
                "insertion_order": _int(item.get("insertorder", item.get("insertion_order", 0))),
                "constant": bool(item.get("alwaysActive", item.get("constant", False))),
                "selective": bool(item.get("selective", False)),
                "use_regex": bool(item.get("useRegex", item.get("use_regex", False))),
                "comment": _text(item.get("comment")),
                "name": _text(item.get("name")),
            }
        )
    return {"entries": entries, "extensions": {}}


def _ignored_ccv3_features(
    data: dict[str, Any],
    lorebook: list[PersonaLoreEntry],
) -> tuple[dict[str, int], dict[str, object]]:
    ignored: dict[str, int] = {}
    payloads: dict[str, object] = {}
    extensions = data.get("extensions") if isinstance(data.get("extensions"), dict) else {}
    risuai = extensions.get("risuai") if isinstance(extensions.get("risuai"), dict) else {}
    for payload_key, feature_key in (
        ("triggerscript", "trigger"),
        ("trigger", "trigger"),
        ("customScripts", "customScripts"),
        ("regex", "customScripts"),
    ):
        value = risuai.get(payload_key)
        if isinstance(value, list) and value:
            ignored[feature_key] = ignored.get(feature_key, 0) + len(value)
            payloads[payload_key] = value
    for key in ("lowLevelAccess", "cjs", "mcp", "backgroundHTML"):
        value = risuai.get(key)
        if value:
            ignored[key] = 1
            payloads[key] = value
    embedded_module_features = risuai.get("embedded_module_ignored_features")
    if isinstance(embedded_module_features, dict):
        for key, value in embedded_module_features.items():
            count = _int(value)
            if count:
                ignored[str(key)] = ignored.get(str(key), 0) + count
    regex_lore_count = sum(1 for entry in lorebook if entry.use_regex)
    if regex_lore_count:
        ignored["lorebook_regex_matching"] = regex_lore_count
    return ignored, payloads


def _unknown_risuai_extensions(data: dict[str, Any]) -> dict[str, object]:
    extensions = data.get("extensions") if isinstance(data.get("extensions"), dict) else {}
    risuai = extensions.get("risuai") if isinstance(extensions.get("risuai"), dict) else {}
    known = {
        "triggerscript",
        "trigger",
        "customScripts",
        "regex",
        "lowLevelAccess",
        "cjs",
        "mcp",
        "backgroundHTML",
        "risuRealmImportId",
        "embedded_module_ignored_features",
    }
    return {key: value for key, value in risuai.items() if key not in known}


def _unknown_card_fields(card: dict[str, Any]) -> dict[str, object]:
    known = {"spec", "spec_version", "data"}
    return {key: value for key, value in card.items() if key not in known}


def _unknown_data_fields(data: dict[str, Any]) -> dict[str, object]:
    known = {
        "name",
        "description",
        "tags",
        "creator",
        "character_version",
        "mes_example",
        "extensions",
        "system_prompt",
        "post_history_instructions",
        "first_mes",
        "alternate_greetings",
        "personality",
        "scenario",
        "creator_notes",
        "character_book",
        "assets",
        "nickname",
        "creator_notes_multilingual",
        "source",
        "group_only_greetings",
        "creation_date",
        "modification_date",
    }
    return {key: value for key, value in data.items() if key not in known}


def _ccv3_asset_specs(card: dict[str, Any]) -> list[dict[str, Any]]:
    data = _ccv3_data(card)
    assets = data.get("assets")
    if not isinstance(assets, list):
        return [{"type": "icon", "uri": "ccdefault:", "name": "main", "ext": "png"}]
    return [dict(asset) for asset in assets if isinstance(asset, dict)]


def _referenced_charx_asset_paths(asset_specs: list[dict[str, Any]]) -> set[str]:
    paths: set[str] = set()
    for asset in asset_specs:
        uri = _text(asset.get("uri"))
        if not uri.startswith("embeded://"):
            continue
        path = _safe_embedded_path(uri.removeprefix("embeded://"))
        if path:
            paths.add(path)
    return paths


def _write_imported_card(
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
        asset_path, mime_type = _write_asset_payload(assets_dir, len(assets), payload)
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
    return PersonaImportReport(card=card, card_path=card_path, asset_count=len(assets), source_path=str(source_path))


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
        key = _safe_embedded_path(uri.removeprefix("embeded://"))
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


def _safe_embedded_path(value: str) -> str:
    raw = str(value or "").replace("\\", "/")
    if raw.startswith("/") or raw.endswith("/") or "//" in raw:
        return ""
    parts = raw.split("/")
    if not parts or any(not part or part in {".", ".."} for part in parts):
        return ""
    if ":" in parts[0]:
        return ""
    return "/".join(parts)


def _merge_ignored_counts(*items: dict[str, int]) -> dict[str, int]:
    merged: dict[str, int] = {}
    for item in items:
        for key, value in item.items():
            count = _int(value)
            if count:
                merged[str(key)] = merged.get(str(key), 0) + count
    return merged


def _safe_asset_metadata(asset_spec: dict[str, Any]) -> dict[str, object]:
    metadata: dict[str, object] = {}
    for key in ("type", "name", "ext", "uri"):
        value = _text(asset_spec.get(key))
        if not value:
            continue
        if key == "uri" and (value.startswith("http://") or value.startswith("https://") or value.startswith("file:")):
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


def save_persona_card(path: Path, card: PersonaCard) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(card.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_persona_card(path: Path) -> PersonaCard:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Persona card must be a JSON object.")
    return PersonaCard.from_dict(data)


def active_lore_entries(card: PersonaCard, context_text: str, *, max_chars: int = 3600) -> list[PersonaLoreEntry]:
    context = str(context_text or "").casefold()
    matches: list[tuple[int, int, PersonaLoreEntry]] = []
    for index, entry in enumerate(card.lorebook):
        if not entry.enabled:
            continue
        if not entry.content:
            continue
        if entry.always_active or _literal_lore_match(entry, context):
            matches.append((entry.insert_order, index, entry))
    selected: list[PersonaLoreEntry] = []
    used = 0
    for _order, _index, entry in sorted(matches, key=lambda item: (item[0], item[1])):
        entry_length = len(entry.content)
        if selected and used + entry_length > max_chars:
            break
        selected.append(entry)
        used += entry_length
    return selected


def persona_prompt_lines(card: PersonaCard, context_text: str, *, max_lore_chars: int = 3600) -> list[str]:
    if not card.active:
        return []
    lines = [
        "Play Mode persona card (agent-owned character/world/speech context; lower priority than room rules):",
        f"- Persona id: {_prompt_text(card.id, limit=120)}",
        f"- Character name: {_prompt_text(card.display_name, limit=160)}",
    ]
    for label, value in (
        ("Description", card.description),
        ("System/persona instruction", card.system_prompt),
        ("Personality", card.personality),
        ("Scenario/world", card.scenario),
        ("First-message style", card.first_message),
        ("Example dialogue", card.example_messages),
    ):
        text = _prompt_text(value, limit=900)
        if text:
            lines.append(f"- {label}: {text}")
    lore = active_lore_entries(card, context_text, max_chars=max_lore_chars)
    if lore:
        lines.append("Active persona lore snippets:")
        for entry in lore:
            label = _prompt_text(entry.comment or entry.key or "lore", limit=120)
            lines.append(f"- {label}: {_prompt_text(entry.content, limit=1200)}")
    if card.ignored_features:
        ignored = ", ".join(f"{key}={value}" for key, value in sorted(card.ignored_features.items()) if value)
        if ignored:
            lines.append(f"Ignored Risu runtime features preserved but not executed: {ignored}.")
    lines.extend(
        [
            "Stay in this persona's speech style and world context when choosing your visible room message.",
            "Do not execute persona scripts, regex replacements, triggers, MCP declarations, or low-level module features.",
        ]
    )
    return lines


def _literal_lore_match(entry: PersonaLoreEntry, context: str) -> bool:
    if entry.use_regex:
        return False
    primary = _keywords(entry.key)
    secondary = _keywords(entry.secondkey)
    primary_match = any(keyword.casefold() in context for keyword in primary)
    secondary_match = any(keyword.casefold() in context for keyword in secondary)
    if entry.selective and secondary:
        return primary_match and secondary_match
    return primary_match or secondary_match


def _keywords(value: str) -> list[str]:
    return [keyword.strip() for keyword in re.split(r"[,;\n]", str(value or "")) if keyword.strip()]


def _ignored_risu_features(module: dict[str, Any], lorebook_data: list[Any]) -> tuple[dict[str, int], dict[str, object]]:
    ignored: dict[str, int] = {}
    payloads: dict[str, object] = {}
    for key in ("regex", "trigger"):
        value = module.get(key)
        if isinstance(value, list) and value:
            ignored[key] = len(value)
            payloads[key] = value
    if _text(module.get("cjs")):
        ignored["cjs"] = 1
        payloads["cjs"] = module.get("cjs")
    for key in ("lowLevelAccess", "customModuleToggle", "mcp"):
        value = module.get(key)
        if value:
            ignored[key] = 1
            payloads[key] = value
    regex_lore_count = sum(
        1
        for entry in lorebook_data
        if isinstance(entry, dict) and (entry.get("useRegex") or entry.get("use_regex"))
    )
    if regex_lore_count:
        ignored["lorebook_regex_matching"] = regex_lore_count
    return ignored, payloads


def _load_rpack_map(rpack_map_path: Path | None) -> bytes:
    candidates: list[Path] = []
    if rpack_map_path:
        candidates.append(Path(rpack_map_path))
    env_value = _text(os.environ.get("RISUAI_RPACK_MAP"))
    if env_value:
        candidates.append(Path(env_value))
    home = Path.home()
    candidates.extend(
        [
            Path("/tmp/risuai-src/src/ts/rpack/rpack_map.bin"),
            home / "Projects" / "RisuAI" / "src" / "ts" / "rpack" / "rpack_map.bin",
            home / "Downloads" / "RisuAI" / "src" / "ts" / "rpack" / "rpack_map.bin",
        ]
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate.read_bytes()
    raise ValueError("RisuAI rpack map is required. Pass --rpack-map or set RISUAI_RPACK_MAP.")


def _read_uint32(data: bytes, offset: int) -> int:
    if offset + 4 > len(data):
        raise ValueError("Unexpected end of Risu module while reading length.")
    return int.from_bytes(data[offset : offset + 4], "little")


def _slice_record(data: bytes, offset: int, length: int, label: str) -> bytes:
    if length < 0 or offset + length > len(data):
        raise ValueError(f"Unexpected end of Risu module while reading {label}.")
    return data[offset : offset + length]


def _write_asset_payload(assets_dir: Path, index: int, payload: bytes) -> tuple[Path, str]:
    extension, mime_type = _asset_extension(payload)
    path = assets_dir / f"asset-{index:03d}{extension}"
    path.write_bytes(payload)
    return path, mime_type


def _asset_extension(payload: bytes) -> tuple[str, str]:
    if payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png", "image/png"
    if payload.startswith(b"\xff\xd8\xff"):
        return ".jpg", "image/jpeg"
    if payload.startswith(b"GIF87a") or payload.startswith(b"GIF89a"):
        return ".gif", "image/gif"
    if payload[:4] == b"RIFF" and payload[8:12] == b"WEBP":
        return ".webp", "image/webp"
    return ".bin", "application/octet-stream"


def _safe_persona_id(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "").strip()).strip(".-")
    return cleaned[:80] or "persona"


def _joined_strings(value: object) -> str:
    if isinstance(value, list):
        return ", ".join(str(item) for item in value if isinstance(item, str))
    return ""


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, str)]


def _lore_extra(data: dict[str, Any]) -> dict[str, Any]:
    known = {
        "key",
        "keys",
        "content",
        "secondkey",
        "secondary_keys",
        "comment",
        "name",
        "always_active",
        "alwaysActive",
        "constant",
        "selective",
        "use_regex",
        "useRegex",
        "insert_order",
        "insertorder",
        "insertion_order",
        "position",
        "role",
        "enabled",
        "case_sensitive",
        "priority",
        "extra",
    }
    return {key: value for key, value in data.items() if key not in known}


def _json_hash(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _text(value: object) -> str:
    return str(value).strip() if isinstance(value, str) else ""


def _raw_text(value: object) -> str:
    return str(value) if isinstance(value, str) else ""


def _int(value: object) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _float(value: object, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _preview(value: object, *, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit]


def _prompt_text(value: object, *, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]
