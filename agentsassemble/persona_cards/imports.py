"""Risu, CCv3, and CharX persona import and asset handling."""

from __future__ import annotations

import base64
from dataclasses import replace
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import shutil
import struct
from typing import Any
import zipfile

from agentsassemble.persona_cards.models import (
    PersonaAsset,
    PersonaCard,
    PersonaImportReport,
    PersonaLoreEntry,
    RisuModulePayload,
)
from agentsassemble.persona_cards.assets import (
    DEFAULT_PERSONA_ROOT,
    MAX_CARD_TEXT_CHUNK_BYTES,
    MAX_CHARX_TOTAL_ASSET_BYTES,
    MAX_DATA_URI_BYTES,
    PNG_SIGNATURE,
    charx_embedded_assets,
    referenced_charx_asset_paths,
    write_asset_payload,
    write_imported_card,
)
from agentsassemble.persona_cards.values import (
    _int,
    _json_hash,
    _raw_text,
    _safe_persona_id,
    _string_list,
    _text,
)
from agentsassemble.persona_cards.rendering import _keywords
from agentsassemble.persona_cards.risu import (
    RISU_ASSET_MARKER,
    RISU_EOF_MARKER,
    RISU_MODULE_MAGIC,
    RISU_MODULE_VERSION,
    decode_rpack,
    ignored_risu_features,
    persona_card_from_risu_module,
    read_risum_module,
    read_risum_module_bytes,
)
from agentsassemble.persona_cards.storage import save_persona_card


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
    return write_imported_card(
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
        referenced_paths = referenced_charx_asset_paths(asset_specs)
        import_assets = charx_embedded_assets(archive, referenced_paths)
        module_payload = _charx_module_payload(archive)
        module_present = "module.risum" in archive.namelist()
    module_ignored_features: dict[str, int] = {}
    module_ignored_payloads: dict[str, object] = {}
    if module_payload is not None and isinstance(card_payload.get("data"), dict):
        data = dict(card_payload["data"])
        module_lore = module_payload.module.get("lorebook")
        module_ignored_features, module_ignored_payloads = ignored_risu_features(
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
    return write_imported_card(
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
        asset_path, mime_type = write_asset_payload(assets_dir, index, asset_payload)
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


def _merge_ignored_counts(*items: dict[str, int]) -> dict[str, int]:
    merged: dict[str, int] = {}
    for item in items:
        for key, value in item.items():
            count = _int(value)
            if count:
                merged[str(key)] = merged.get(str(key), 0) + count
    return merged
