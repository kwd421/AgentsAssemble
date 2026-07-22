"""Persona card domain models and safe summaries."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Any

from agentsassemble.persona_cards.values import (
    _float,
    _int,
    _joined_strings,
    _lore_extra,
    _raw_text,
    _string_list,
    _text,
)


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
    speech_style: dict[str, object] = field(default_factory=dict)
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
            "speech_style": self.speech_style,
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
        speech_style = data.get("speech_style") if isinstance(data.get("speech_style"), dict) else {}
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
            speech_style=_safe_speech_style(speech_style),
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
            "source": _safe_persona_source_summary(self.source),
            "lorebook_count": len(self.lorebook),
            "asset_count": len(self.assets),
            "ignored_features": self.ignored_features,
            "tag_count": len(self.tags),
            "content_lengths": {
                "system_prompt": len(self.system_prompt),
                "personality": len(self.personality),
                "scenario": len(self.scenario),
                "post_history_instructions": len(self.post_history_instructions),
            },
            "speech_style": _safe_speech_style_summary(self.speech_style),
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
            "card_path": _safe_report_path(self.card_path),
            "source_path": _safe_source_path_summary(self.source_path),
            "lorebook_count": self.lorebook_count,
            "asset_count": self.asset_count,
            "ignored_features": self.card.ignored_features,
        }


def _safe_persona_source_summary(source: dict[str, object]) -> dict[str, object]:
    if not isinstance(source, dict) or not source:
        return {}
    safe: dict[str, object] = {}
    for key in ("kind", "container", "format", "spec", "spec_version"):
        value = source.get(key)
        if isinstance(value, str) and value.strip():
            cleaned = re.sub(r"[^A-Za-z0-9_.:-]+", "-", value.strip()).strip(".-:")
            if cleaned:
                safe[key] = cleaned[:80]
    if "imported_at" in source:
        safe["has_import_timestamp"] = True
    return safe


def _safe_speech_style(value: dict[str, object]) -> dict[str, object]:
    if not isinstance(value, dict) or not value:
        return {}
    safe: dict[str, object] = {}
    for key in ("tone", "cadence", "collaboration_style", "role_label"):
        text = _text(value.get(key))
        if text:
            safe[key] = text
    for key in ("do", "do_not"):
        items = _string_list(value.get(key))
        if items:
            safe[key] = items[:8]
    return safe


def _safe_speech_style_summary(value: dict[str, object]) -> dict[str, object]:
    safe = _safe_speech_style(value)
    if not safe:
        return {"configured": False, "do": 0, "do_not": 0}
    return {
        "configured": True,
        "do": len(safe.get("do", [])) if isinstance(safe.get("do"), list) else 0,
        "do_not": len(safe.get("do_not", [])) if isinstance(safe.get("do_not"), list) else 0,
    }


def _safe_report_path(path: Path) -> str:
    if not path:
        return ""
    parts = Path(path).parts
    for marker in ("personas", "persona-smoke", "meetings"):
        if marker in parts:
            index = parts.index(marker)
            return str(Path(*parts[index:]))
    name = Path(path).name
    return name[:120] if name else ""


def _safe_source_path_summary(source_path: str) -> dict[str, object]:
    if not source_path:
        return {}
    suffix = Path(source_path).suffix
    summary: dict[str, object] = {"provided": True}
    if suffix and re.fullmatch(r"\.[A-Za-z0-9]{1,12}", suffix):
        summary["suffix"] = suffix.lower()
    return summary


@dataclass(frozen=True)
class PersonaLoreScanResult:
    entries: list[PersonaLoreEntry]
    state: dict[str, object] = field(default_factory=dict)
    ignored_features: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class PersonaPromptRender:
    lines: list[str]
    scan: PersonaLoreScanResult
    mode: str = "on"
    surface: str = "play_speech"
