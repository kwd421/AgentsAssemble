from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


RISU_MODULE_MAGIC = 111
RISU_MODULE_VERSION = 0
RISU_ASSET_MARKER = 1
RISU_EOF_MARKER = 0
DEFAULT_PERSONA_ROOT = "personas"


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
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PersonaLoreEntry:
        return cls(
            key=_text(data.get("key")),
            content=_raw_text(data.get("content")),
            secondkey=_text(data.get("secondkey")),
            comment=_text(data.get("comment")),
            always_active=bool(data.get("always_active", data.get("alwaysActive", False))),
            selective=bool(data.get("selective", False)),
            use_regex=bool(data.get("use_regex", data.get("useRegex", False))),
            insert_order=_int(data.get("insert_order", data.get("insertorder", 0))),
            position=_text(data.get("position")),
            role=_text(data.get("role")),
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
    example_messages: str = ""
    talkativeness: float = 0.66
    active: bool = True
    lorebook: list[PersonaLoreEntry] = field(default_factory=list)
    assets: list[PersonaAsset] = field(default_factory=list)
    source: dict[str, object] = field(default_factory=dict)
    ignored_features: dict[str, int] = field(default_factory=dict)
    ignored_payloads: dict[str, object] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "display_name": self.display_name,
            "description": self.description,
            "system_prompt": self.system_prompt,
            "personality": self.personality,
            "scenario": self.scenario,
            "first_message": self.first_message,
            "example_messages": self.example_messages,
            "talkativeness": self.talkativeness,
            "active": self.active,
            "lorebook": [entry.to_dict() for entry in self.lorebook],
            "assets": [asset.to_dict() for asset in self.assets],
            "source": self.source,
            "ignored_features": self.ignored_features,
            "ignored_payloads": self.ignored_payloads,
            "tags": list(self.tags),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PersonaCard:
        lorebook = data.get("lorebook") if isinstance(data.get("lorebook"), list) else []
        assets = data.get("assets") if isinstance(data.get("assets"), list) else []
        source = data.get("source") if isinstance(data.get("source"), dict) else {}
        ignored_features = data.get("ignored_features") if isinstance(data.get("ignored_features"), dict) else {}
        ignored_payloads = data.get("ignored_payloads") if isinstance(data.get("ignored_payloads"), dict) else {}
        tags = data.get("tags") if isinstance(data.get("tags"), list) else []
        return cls(
            id=_text(data.get("id")) or "persona",
            display_name=_text(data.get("display_name")) or _text(data.get("name")) or "Persona",
            description=_text(data.get("description")),
            system_prompt=_text(data.get("system_prompt")),
            personality=_text(data.get("personality")),
            scenario=_text(data.get("scenario")),
            first_message=_text(data.get("first_message")),
            example_messages=_text(data.get("example_messages")),
            talkativeness=_float(data.get("talkativeness"), 0.66),
            active=bool(data.get("active", True)),
            lorebook=[PersonaLoreEntry.from_dict(entry) for entry in lorebook if isinstance(entry, dict)],
            assets=[PersonaAsset.from_dict(asset) for asset in assets if isinstance(asset, dict)],
            source=dict(source),
            ignored_features={str(key): _int(value) for key, value in ignored_features.items()},
            ignored_payloads=dict(ignored_payloads),
            tags=[str(tag) for tag in tags if isinstance(tag, str)],
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

    return RisuModulePayload(module=dict(module), asset_payloads=assets, source_path=str(path))


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
        example_messages=_text(module.get("exampleMessage") or module.get("example_messages")),
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
    regex_lore_count = sum(1 for entry in lorebook_data if isinstance(entry, dict) and entry.get("useRegex"))
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
