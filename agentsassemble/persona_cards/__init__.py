from __future__ import annotations

import json
import os
import re
import shutil
import base64
import hashlib
import struct
import zipfile
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agentsassemble.persona_cards.models import (
    PersonaAsset,
    PersonaCard,
    PersonaImportReport,
    PersonaLoreEntry,
    PersonaLoreScanResult,
    PersonaPromptRender,
    RisuModulePayload,
    _safe_speech_style,
)
from agentsassemble.persona_cards.values import (
    _float,
    _int,
    _joined_strings,
    _json_hash,
    _lore_extra,
    _preview,
    _prompt_text,
    _raw_text,
    _safe_persona_id,
    _string_list,
    _text,
)


RISU_MODULE_MAGIC = 111
RISU_MODULE_VERSION = 0
RISU_ASSET_MARKER = 1
RISU_EOF_MARKER = 0
DEFAULT_PERSONA_ROOT = "personas"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_CARD_TEXT_CHUNK_BYTES = 5 * 1024 * 1024
MAX_DATA_URI_BYTES = 50 * 1024 * 1024
MAX_CHARX_TOTAL_ASSET_BYTES = 60 * 1024 * 1024
MAX_LORE_RECURSIVE_SCAN_DEPTH = 8




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
        lore_settings=_lore_settings_from_risu_module(module),
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


def _lore_settings_from_risu_module(module: dict[str, Any]) -> dict[str, object]:
    settings: dict[str, object] = {}
    for source_key, target_key in (
        ("scanDepth", "scan_depth"),
        ("scan_depth", "scan_depth"),
        ("tokenBudget", "token_budget"),
        ("token_budget", "token_budget"),
        ("recursiveScanning", "recursive_scanning"),
        ("recursive_scanning", "recursive_scanning"),
        ("fullWordMatching", "full_word_matching"),
        ("full_word_matching", "full_word_matching"),
    ):
        if source_key in module:
            settings[target_key] = module[source_key]
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
    return scan_persona_lore(card, context_text, max_chars=max_chars).entries


def scan_persona_lore(
    card: PersonaCard,
    recent_messages: str | list[str],
    *,
    state: dict[str, object] | None = None,
    max_chars: int | None = None,
    recursion_limit: int | None = None,
) -> PersonaLoreScanResult:
    next_state = _persona_runtime_state(state)
    ignored_features: dict[str, int] = {}
    token_budget = _optional_int(card.lore_settings.get("token_budget"))
    token_budget = 3600 if token_budget is None else token_budget
    char_budget = max_chars if max_chars is not None else token_budget
    scan_depth = recursion_limit if recursion_limit is not None else _optional_int(card.lore_settings.get("scan_depth"))
    scan_depth = 1 if scan_depth is None else scan_depth
    scan_depth = max(1, scan_depth)
    context = _recent_messages_text(recent_messages, limit=scan_depth)
    message_count = _recent_message_count(recent_messages)
    recursive_scanning = bool(card.lore_settings.get("recursive_scanning", False))
    recursive_rounds = min(scan_depth, MAX_LORE_RECURSIVE_SCAN_DEPTH) if recursive_scanning else 1
    full_word_matching = bool(card.lore_settings.get("full_word_matching", False))

    selected_indexes: set[int] = set()
    selected_decorators: dict[int, dict[str, Any]] = {}
    search_text = context
    for _scan_round in range(recursive_rounds):
        added = False
        for index, entry in enumerate(card.lorebook):
            if index in selected_indexes:
                continue
            decorators = _lore_entry_decorators(entry)
            if _persona_lore_entry_matches(
                entry,
                search_text,
                next_state,
                index,
                ignored_features,
                decorators=decorators,
                message_count=message_count,
                full_word_matching=full_word_matching,
            ):
                selected_indexes.add(index)
                selected_decorators[index] = decorators
                added = True
        if not added:
            break
        search_text = "\n".join([context] + [_visible_lore_content(card.lorebook[index]) for index in sorted(selected_indexes)])

    selected_pairs = [
        (index, _visible_lore_entry(card.lorebook[index]))
        for index in sorted(selected_indexes, key=lambda item: (card.lorebook[item].insert_order, item))
    ]
    selected_pairs = _fit_lore_budget(selected_pairs, char_budget)
    for index, _entry in selected_pairs:
        decorators = selected_decorators.get(index) or _lore_entry_decorators(card.lorebook[index])
        if decorators["keep_activate_after_match"]:
            next_state.setdefault("sticky_lore", {})[str(index)] = True
        if decorators["dont_activate_after_match"]:
            next_state.setdefault("cooldown_lore", {})[str(index)] = True
    return PersonaLoreScanResult(entries=[entry for _index, entry in selected_pairs], state=next_state, ignored_features=ignored_features)


def render_persona_prompt(
    card: PersonaCard,
    *,
    recent_messages: str | list[str] = "",
    user_name: str = "user",
    persona: str = "",
    variables: dict[str, object] | None = None,
    state: dict[str, object] | None = None,
    mode: str = "on",
    surface: str = "play_speech",
    first_message_index: int = 0,
    max_lore_chars: int = 3600,
) -> PersonaPromptRender:
    if not card.active or mode == "off":
        empty_scan = PersonaLoreScanResult(entries=[], state=_persona_runtime_state(state))
        return PersonaPromptRender(lines=[], scan=empty_scan, mode=mode, surface=surface)
    if surface == "artifact":
        empty_scan = PersonaLoreScanResult(
            entries=[],
            state=_persona_runtime_state(state),
            ignored_features=dict(card.ignored_features),
        )
        return PersonaPromptRender(
            lines=_persona_artifact_contract_lines(card),
            scan=empty_scan,
            mode=mode,
            surface=surface,
        )
    if mode == "work_speech_only" or surface == "work_speech":
        empty_scan = PersonaLoreScanResult(
            entries=[],
            state=_persona_runtime_state(state),
            ignored_features=dict(card.ignored_features),
        )
        return PersonaPromptRender(
            lines=_persona_work_speech_capsule_lines(card, recent_messages=recent_messages),
            scan=empty_scan,
            mode=mode,
            surface=surface,
        )

    scan = scan_persona_lore(card, recent_messages, state=state, max_chars=max_lore_chars)
    lines = [
        "Play Mode persona card (agent-owned character/world/speech context; lower priority than room rules):",
        f"- Persona id: {_prompt_text(card.id, limit=120)}",
        f"- Character name: {_prompt_text(card.display_name, limit=160)}",
    ]
    _append_prompt_block(lines, "System/persona instruction", card.system_prompt, card, user_name, persona, variables)
    _append_prompt_block(lines, "Description", card.description, card, user_name, persona, variables)
    _append_prompt_block(lines, "Personality", card.personality, card, user_name, persona, variables)
    _append_prompt_block(lines, "Scenario/world", card.scenario, card, user_name, persona, variables)
    if scan.entries:
        lines.append("Active persona lore snippets:")
        for entry in scan.entries:
            label = _prompt_text(entry.comment or entry.key or "lore", limit=120)
            content = replace_persona_variables(entry.content, card, user_name=user_name, persona=persona, variables=variables)
            lines.append(f"- {label}: {_prompt_text(content, limit=1200)}")
    _append_prompt_block(lines, "Example dialogue", card.example_messages, card, user_name, persona, variables)
    _append_prompt_block(
        lines,
        "First-message style",
        _selected_first_message(card, first_message_index),
        card,
        user_name,
        persona,
        variables,
    )
    context_text = _prompt_text(_recent_messages_text(recent_messages), limit=1200)
    if context_text:
        lines.append(f"- Recent room context: {context_text}")
    _append_prompt_block(lines, "Post-history instruction", card.post_history_instructions, card, user_name, persona, variables)
    if card.ignored_features:
        ignored = ", ".join(f"{key}={value}" for key, value in sorted(card.ignored_features.items()) if value)
        if ignored:
            lines.append(f"Ignored Risu runtime features preserved but not executed: {ignored}.")
    lines.extend(_persona_surface_instructions(surface))
    return PersonaPromptRender(lines=lines, scan=scan, mode=mode, surface=surface)


def persona_prompt_lines(
    card: PersonaCard,
    context_text: str,
    *,
    first_message_index: int = 0,
    max_lore_chars: int = 3600,
) -> list[str]:
    return render_persona_prompt(
        card,
        recent_messages=context_text,
        first_message_index=first_message_index,
        max_lore_chars=max_lore_chars,
    ).lines


def replace_persona_variables(
    text: str,
    card: PersonaCard,
    *,
    user_name: str = "user",
    persona: str = "",
    variables: dict[str, object] | None = None,
) -> str:
    value = str(text or "")
    replacements = {
        "{{char}}": card.display_name,
        "<char>": card.display_name,
        "<bot>": card.display_name,
        "{{user}}": user_name,
        "{{persona}}": persona,
    }
    for marker, replacement in replacements.items():
        value = value.replace(marker, str(replacement))
    slot_values = variables or {}

    def replace_slot(match: re.Match[str]) -> str:
        key = match.group(1).strip()
        if key in slot_values:
            return str(slot_values[key])
        return match.group(0)

    return re.sub(r"\{\{slot::([^{}]+?)\}\}", replace_slot, value)


def _persona_lore_entry_matches(
    entry: PersonaLoreEntry,
    context: str,
    state: dict[str, object],
    index: int,
    ignored_features: dict[str, int],
    *,
    decorators: dict[str, Any],
    message_count: int,
    full_word_matching: bool,
) -> bool:
    if not entry.enabled:
        return False
    if not entry.content:
        return False
    if entry.use_regex:
        ignored_features["regex_lore"] = ignored_features.get("regex_lore", 0) + 1
        return False
    sticky_lore = state.setdefault("sticky_lore", {})
    if sticky_lore.get(str(index)):
        return True
    cooldown_lore = state.setdefault("cooldown_lore", {})
    if cooldown_lore.get(str(index)):
        return False
    activate_after = decorators["activate_only_after"]
    if activate_after is not None and message_count < activate_after:
        return False
    activate_every = decorators["activate_only_every"]
    if activate_every is not None and (activate_every <= 0 or message_count % activate_every != 0):
        return False
    probability = decorators["probability"]
    if probability is not None and probability <= 0:
        return False
    if probability is not None and probability < 100:
        seed = f"{entry.key}\n{entry.content}\n{index}".encode("utf-8", errors="replace")
        if int(hashlib.sha256(seed).hexdigest()[:8], 16) % 100 >= probability:
            return False
    if entry.always_active:
        return True
    return _literal_lore_match(entry, context, full_word_matching=full_word_matching, decorators=decorators)


def _literal_lore_match(entry: PersonaLoreEntry, context: str, *, full_word_matching: bool, decorators: dict[str, Any]) -> bool:
    primary = _keywords(entry.key)
    secondary = _keywords(entry.secondkey)
    if not primary and not secondary:
        return False
    use_full_word = full_word_matching
    if decorators["match_full_word"]:
        use_full_word = True
    if decorators["match_partial_word"]:
        use_full_word = False
    if entry.case_sensitive:
        haystack = context
        primary_match = any(_literal_keyword_match(haystack, keyword, case_sensitive=True, full_word=use_full_word) for keyword in primary)
        secondary_match = any(_literal_keyword_match(haystack, keyword, case_sensitive=True, full_word=use_full_word) for keyword in secondary)
    else:
        haystack = context.casefold()
        primary_match = any(_literal_keyword_match(haystack, keyword.casefold(), case_sensitive=False, full_word=use_full_word) for keyword in primary)
        secondary_match = any(_literal_keyword_match(haystack, keyword.casefold(), case_sensitive=False, full_word=use_full_word) for keyword in secondary)
    if entry.selective and secondary:
        return primary_match and secondary_match
    return primary_match or secondary_match


def _literal_keyword_match(haystack: str, keyword: str, *, case_sensitive: bool, full_word: bool) -> bool:
    if not keyword:
        return False
    if not full_word:
        return keyword in haystack
    flags = 0 if case_sensitive else re.IGNORECASE
    return bool(re.search(rf"(?<!\w){re.escape(keyword)}(?!\w)", haystack, flags=flags))


def _recent_messages_text(recent_messages: str | list[str], *, limit: int | None = None) -> str:
    if isinstance(recent_messages, list):
        messages = [str(message) for message in recent_messages if str(message)]
        if limit is not None:
            messages = messages[-limit:]
        return "\n".join(messages)
    return str(recent_messages or "")


def _recent_message_count(recent_messages: str | list[str]) -> int:
    if isinstance(recent_messages, list):
        return len([message for message in recent_messages if str(message)])
    return 1 if str(recent_messages or "") else 0


def _persona_runtime_state(state: dict[str, object] | None) -> dict[str, object]:
    result = dict(state or {})
    sticky_lore = result.get("sticky_lore")
    result["sticky_lore"] = dict(sticky_lore) if isinstance(sticky_lore, dict) else {}
    cooldown_lore = result.get("cooldown_lore")
    result["cooldown_lore"] = dict(cooldown_lore) if isinstance(cooldown_lore, dict) else {}
    return result


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _fit_lore_budget(entries: list[tuple[int, PersonaLoreEntry]], char_budget: int) -> list[tuple[int, PersonaLoreEntry]]:
    if char_budget <= 0:
        return []
    selected_positions: set[int] = set()
    fallback: tuple[int, tuple[int, PersonaLoreEntry]] | None = None
    used = 0
    budget_order = sorted(enumerate(entries), key=lambda item: (-item[1][1].priority, item[1][1].insert_order, item[0]))
    for position, (source_index, entry) in budget_order:
        entry_length = len(entry.content)
        if used + entry_length <= char_budget:
            selected_positions.add(position)
            used += entry_length
            continue
        if fallback is None:
            fallback = (position, (source_index, replace(entry, content=entry.content[:char_budget])))
    if not selected_positions and fallback is not None:
        return [fallback[1]]
    return [pair for position, pair in enumerate(entries) if position in selected_positions]


def _visible_lore_entry(entry: PersonaLoreEntry) -> PersonaLoreEntry:
    return replace(entry, content=_visible_lore_content(entry))


def _visible_lore_content(entry: PersonaLoreEntry) -> str:
    lines = str(entry.content or "").splitlines()
    while lines and lines[0].strip().startswith("@@"):
        lines.pop(0)
    return "\n".join(lines)


def _lore_entry_decorators(entry: PersonaLoreEntry) -> dict[str, Any]:
    decorators: dict[str, Any] = {
        "keep_activate_after_match": False,
        "dont_activate_after_match": False,
        "activate_only_after": None,
        "activate_only_every": None,
        "match_full_word": False,
        "match_partial_word": False,
        "probability": None,
    }
    for line in str(entry.content or "").splitlines():
        stripped = line.strip()
        if not stripped.startswith("@@"):
            break
        parts = stripped[2:].split()
        if not parts:
            continue
        name = parts[0]
        if name == "keep_activate_after_match":
            decorators["keep_activate_after_match"] = True
        elif name == "dont_activate_after_match":
            decorators["dont_activate_after_match"] = True
        elif name == "activate_only_after" and len(parts) >= 2:
            decorators["activate_only_after"] = _optional_int(parts[1])
        elif name == "activate_only_every" and len(parts) >= 2:
            decorators["activate_only_every"] = _optional_int(parts[1])
        elif name == "match_full_word":
            decorators["match_full_word"] = True
        elif name == "match_partial_word":
            decorators["match_partial_word"] = True
        elif name == "probability" and len(parts) >= 2:
            decorators["probability"] = _optional_int(parts[1])
    return decorators


def _append_prompt_block(
    lines: list[str],
    label: str,
    value: str,
    card: PersonaCard,
    user_name: str,
    persona: str,
    variables: dict[str, object] | None,
) -> None:
    text = replace_persona_variables(value, card, user_name=user_name, persona=persona, variables=variables)
    prompt_text = _prompt_text(text, limit=900)
    if prompt_text:
        lines.append(f"- {label}: {prompt_text}")


def _append_raw_prompt_block(lines: list[str], label: str, value: str) -> None:
    text = _prompt_text(value, limit=900)
    if text:
        lines.append(f"- {label}: {text}")


def _persona_surface_instructions(surface: str) -> list[str]:
    if surface == "artifact":
        return [
            "Use persona context only to understand stance and priorities; keep artifacts professional.",
            "Do not put roleplay narration, explicit lore text, or unresolved persona variables into code, docs, commits, or decisions.",
        ]
    return [
        "Stay in this persona's speech style and world context when choosing your visible room message.",
        "Do not execute persona scripts, regex replacements, triggers, MCP declarations, or low-level module features.",
    ]


def _persona_artifact_contract_lines(card: PersonaCard) -> list[str]:
    lines = [
        "Character Mode artifact surface: persona card bodies are withheld.",
        f"- Persona id: {_prompt_text(card.id, limit=120)}",
        f"- Character name: {_prompt_text(card.display_name, limit=160)}",
        "Write the artifact in a professional project voice.",
        "Do not include roleplay narration, explicit lore text, unresolved persona variables, "
        "or card-only private context.",
    ]
    if card.ignored_features:
        ignored = ", ".join(
            f"{key}={value}" for key, value in sorted(card.ignored_features.items()) if value
        )
        if ignored:
            lines.append(f"Ignored Risu runtime features are preserved in storage but not executed: {ignored}.")
    return lines


def _persona_work_speech_capsule_lines(card: PersonaCard, *, recent_messages: str | list[str]) -> list[str]:
    speech_style = _safe_speech_style(card.speech_style)
    lines = [
        "Character speech style (safe work_speech_only capsule; raw lore/world/body text withheld):",
        f"- Persona id: {_prompt_text(card.id, limit=120)}",
        f"- Character name: {_prompt_text(card.display_name, limit=160)}",
        "- Keep the character's visible collaboration style when speaking in the room.",
        "- Do not treat private persona lore as meeting evidence or copy card body text into Work Mode artifacts.",
    ]
    for key, label in (
        ("role_label", "Role label"),
        ("tone", "Tone"),
        ("cadence", "Cadence"),
        ("collaboration_style", "Collaboration style"),
    ):
        value = speech_style.get(key)
        if isinstance(value, str) and value:
            lines.append(f"- {label}: {_prompt_text(value, limit=240)}")
    for key, label in (("do", "Do"), ("do_not", "Do not")):
        values = speech_style.get(key)
        if isinstance(values, list) and values:
            joined = "; ".join(_prompt_text(item, limit=180) for item in values if isinstance(item, str) and item)
            if joined:
                lines.append(f"- {label}: {joined}")
    context_text = _prompt_text(_recent_messages_text(recent_messages), limit=1200)
    if context_text:
        lines.append(f"- Recent room context: {context_text}")
    if card.ignored_features:
        ignored = ", ".join(
            f"{key}={value}" for key, value in sorted(card.ignored_features.items()) if value
        )
        if ignored:
            lines.append(f"Ignored Risu runtime features are preserved in storage but not executed: {ignored}.")
    return lines


def _selected_first_message(card: PersonaCard, first_message_index: int) -> str:
    try:
        index = int(first_message_index)
    except (TypeError, ValueError):
        index = 0
    if index < 0:
        return ""
    if index == 0:
        return card.first_message
    alternate_index = index - 1
    if 0 <= alternate_index < len(card.alternate_greetings):
        return card.alternate_greetings[alternate_index]
    return card.first_message


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
