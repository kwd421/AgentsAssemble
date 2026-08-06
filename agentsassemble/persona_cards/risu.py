"""Risu module decoding and normalized persona conversion."""

from __future__ import annotations

from datetime import UTC, datetime
import json
import os
from pathlib import Path
from typing import Any

from agentsassemble.persona_cards.models import (
    PersonaAsset,
    PersonaCard,
    PersonaLoreEntry,
    RisuModulePayload,
)
from agentsassemble.persona_cards.values import (
    _safe_persona_id,
    _string_list,
    _text,
)


RISU_MODULE_MAGIC = 111
RISU_MODULE_VERSION = 0
RISU_ASSET_MARKER = 1
RISU_EOF_MARKER = 0
MAX_RISUM_FILE_BYTES = 16 * 1024 * 1024
MAX_RISUM_MAIN_BYTES = 5 * 1024 * 1024
MAX_RISUM_ASSET_BYTES = 8 * 1024 * 1024
MAX_RISUM_ASSET_COUNT = 256


def read_risum_module(path: Path, *, rpack_map_path: Path | None = None) -> RisuModulePayload:
    if Path(path).stat().st_size > MAX_RISUM_FILE_BYTES:
        raise ValueError("Risu module file is too large.")
    data = Path(path).read_bytes()
    return read_risum_module_bytes(data, rpack_map_path=rpack_map_path, source_path=str(path))


def read_risum_module_bytes(
    data: bytes,
    *,
    rpack_map_path: Path | None = None,
    source_path: str = "",
) -> RisuModulePayload:
    if len(data) > MAX_RISUM_FILE_BYTES:
        raise ValueError("Risu module file is too large.")
    if len(data) < 6:
        raise ValueError("Risu module file is too small.")
    if data[0] != RISU_MODULE_MAGIC:
        raise ValueError("Risu module magic byte is invalid.")
    if data[1] != RISU_MODULE_VERSION:
        raise ValueError(f"Unsupported Risu module version: {data[1]}")
    rpack_map = _load_rpack_map(rpack_map_path)
    offset = 2
    main_length = _read_uint32(data, offset)
    if main_length > MAX_RISUM_MAIN_BYTES:
        raise ValueError("Risu module payload exceeds the safe size limit.")
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
    total_asset_bytes = 0
    while offset < len(data):
        marker = data[offset]
        offset += 1
        if marker == RISU_EOF_MARKER:
            break
        if marker != RISU_ASSET_MARKER:
            raise ValueError(f"Unsupported Risu module record marker: {marker}")
        if len(assets) >= MAX_RISUM_ASSET_COUNT:
            raise ValueError("Risu module contains too many asset records.")
        asset_length = _read_uint32(data, offset)
        total_asset_bytes += asset_length
        if total_asset_bytes > MAX_RISUM_ASSET_BYTES:
            raise ValueError("Risu module assets exceed the safe size limit.")
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
    ignored_features, ignored_payloads = ignored_risu_features(module, lorebook_data)
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


def ignored_risu_features(
    module: dict[str, Any],
    lorebook_data: list[Any],
) -> tuple[dict[str, int], dict[str, object]]:
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
