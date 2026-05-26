from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agentsassemble.persona_cards import PersonaCard, load_persona_card


ARTIFACT_SURFACES = {
    "action_items",
    "commit_message",
    "decision",
    "delegate_packet",
    "documentation",
    "json_artifact",
    "return_packet",
    "return_packet_summary",
    "shared_memory",
    "task",
    "transcript",
}
SPEECH_SURFACES = {"lobby", "official_speech", "review_comment", "side_chat"}
_VARIABLE_RE = re.compile(r"(\{\{\s*(?:char|user|persona)\s*\}\}|\{\{\s*slot::[^}]+\}\}|<char>|<bot>)", re.IGNORECASE)
_ROLEPLAY_RE = re.compile(
    r"(?<!\*)\*\s*(?:smiles?|smirks?|waves?|laughs?|giggles?|sighs?|nods?|shrugs?|whispers?|blushes?|leans?|grins?|tilts?|stares?|glares?|frowns?|looks? away|bows?|pauses?|rolls? (?:his|her|their)? ?eyes|crosses? (?:his|her|their)? ?arms|uncrosses? (?:his|her|their)? ?arms|taps? (?:his|her|their)? ?(?:finger|foot|pen)|gestures?)\b[^*\n]{0,80}\*(?!\*)",
    re.IGNORECASE,
)
_CHARACTER_BADGE_RE = re.compile(
    r"\b(?:Character\s*:\s*(?:ON|OFF|Work speech)|Character speech style|Persona id\s*:|Character name\s*:)",
    re.IGNORECASE,
)
_NSFW_RE = re.compile(r"\b(NSFW|adult\s+marker|explicit\s+sexual|sexual\s+content|NSFW_MARKER|ADULT_MARKER)\b", re.IGNORECASE)


@dataclass(frozen=True)
class _CardMarker:
    field: str
    text: str


def scan_persona_artifact_text(
    text: str,
    *,
    surface: str,
    cards: list[PersonaCard] | tuple[PersonaCard, ...] = (),
    ignored_feature_names: list[str] | tuple[str, ...] = (),
) -> dict[str, object]:
    violations: list[dict[str, object]] = []
    body = str(text or "")
    _append_regex_violation(violations, "unreplaced_variable", _VARIABLE_RE, body)
    _append_regex_violation(violations, "roleplay_narration", _ROLEPLAY_RE, body)
    _append_regex_violation(violations, "character_badge", _CHARACTER_BADGE_RE, body)
    _append_regex_violation(violations, "nsfw_marker", _NSFW_RE, body)
    _append_ignored_feature_violations(violations, body, cards=cards, ignored_feature_names=ignored_feature_names)
    _append_raw_card_text_violations(violations, body, cards=cards)
    violation_count = sum(int(violation.get("count") or 0) for violation in violations)
    return {
        "surface": _safe_surface(surface),
        "status": "violation" if violation_count else "pass",
        "violation_count": violation_count,
        "violations": violations,
    }


def apply_persona_artifact_contract_report(
    meeting_dir: Path,
    meeting: dict[str, object],
) -> dict[str, object]:
    if not _has_character_artifact_scope(meeting):
        return _empty_contract_report()
    report = scan_meeting_artifacts(meeting_dir, meeting)
    meeting["persona_artifact_contract"] = report
    event_log = meeting.get("event_log") if isinstance(meeting.get("event_log"), list) else []
    meeting["event_log"] = [
        event
        for event in event_log
        if not (isinstance(event, dict) and event.get("kind") == "persona_artifact_contract")
    ]
    meeting["event_log"].append(
        {
            "created_at": datetime.now(UTC).isoformat(),
            "scope": "meeting",
            "kind": "persona_artifact_contract",
            "actor_id": "system",
            "message": "Persona artifact contract scanned.",
            "payload": {
                "status": report["status"],
                "artifact_count": report["artifact_count"],
                "violation_count": report["violation_count"],
            },
        }
    )
    return report


def _has_character_artifact_scope(meeting: dict[str, object]) -> bool:
    character_mode = meeting.get("character_mode") if isinstance(meeting.get("character_mode"), dict) else {}
    agents = character_mode.get("agents") if isinstance(character_mode.get("agents"), list) else []
    return any(isinstance(agent, dict) and str(agent.get("mode") or "off") != "off" for agent in agents)


def _empty_contract_report() -> dict[str, object]:
    return {
        "version": 1,
        "status": "pass",
        "artifact_count": 0,
        "violation_count": 0,
        "artifacts": [],
    }


def scan_meeting_artifacts(meeting_dir: Path, meeting: dict[str, object]) -> dict[str, object]:
    cards, ignored_feature_names, card_issues = _active_persona_cards(meeting_dir, meeting)
    artifacts = []
    total_violations = len(card_issues)
    for artifact_path, surface in _artifact_files(meeting_dir):
        try:
            text = _artifact_text(artifact_path, surface)
        except OSError:
            continue
        artifact = scan_persona_artifact_text(
            text,
            surface=surface,
            cards=cards,
            ignored_feature_names=ignored_feature_names,
        )
        relative_path = _artifact_relative_path(meeting_dir, artifact_path)
        total_violations += int(artifact["violation_count"])
        artifacts.append(
            {
                "path": relative_path,
                "surface": artifact["surface"],
                "status": artifact["status"],
                "violation_count": artifact["violation_count"],
                "codes": [str(violation.get("code") or "") for violation in artifact["violations"]],
            }
        )
    return {
        "version": 1,
        "status": "violation" if total_violations else "pass",
        "artifact_count": len(artifacts),
        "violation_count": total_violations,
        "card_issues": card_issues,
        "artifacts": artifacts,
    }


def _artifact_text(path: Path, surface: str) -> str:
    raw = path.read_text(encoding="utf-8")
    if surface != "json_artifact":
        return raw
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    return "\n".join(_json_strings(payload))


def _json_strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        strings: list[str] = []
        for item in value.values():
            strings.extend(_json_strings(item))
        return strings
    if isinstance(value, list):
        strings = []
        for item in value:
            strings.extend(_json_strings(item))
        return strings
    return []


def _append_regex_violation(violations: list[dict[str, object]], code: str, pattern: re.Pattern[str], text: str) -> None:
    count = len(pattern.findall(text))
    if count:
        violations.append({"code": code, "count": count})


def _append_ignored_feature_violations(
    violations: list[dict[str, object]],
    text: str,
    *,
    cards: list[PersonaCard] | tuple[PersonaCard, ...],
    ignored_feature_names: list[str] | tuple[str, ...],
) -> None:
    names = set(ignored_feature_names)
    for card in cards:
        names.update(str(name) for name in card.ignored_features)
    normalized_text = text.casefold()
    count = 0
    for name in sorted(names):
        normalized_name = str(name).strip().casefold()
        if len(normalized_name) < 3:
            continue
        if re.search(rf"(?<![A-Za-z0-9_]){re.escape(normalized_name)}(?![A-Za-z0-9_])", normalized_text):
            count += 1
    if count:
        violations.append({"code": "ignored_feature_name", "count": count})


def _append_raw_card_text_violations(
    violations: list[dict[str, object]],
    text: str,
    *,
    cards: list[PersonaCard] | tuple[PersonaCard, ...],
) -> None:
    normalized_text = _normalized_body(text)
    matched_fields = set()
    for card in cards:
        for marker in _card_markers(card):
            if any(segment in normalized_text for segment in _card_marker_segments(marker)):
                matched_fields.add(marker.field)
    if matched_fields:
        violations.append(
            {
                "code": "raw_card_text",
                "count": len(matched_fields),
                "fields": sorted(matched_fields),
            }
        )


def _card_markers(card: PersonaCard) -> list[_CardMarker]:
    markers = [
        _CardMarker("description", card.description),
        _CardMarker("system_prompt", card.system_prompt),
        _CardMarker("personality", card.personality),
        _CardMarker("scenario", card.scenario),
        _CardMarker("first_message", card.first_message),
        _CardMarker("example_messages", card.example_messages),
        _CardMarker("post_history_instructions", card.post_history_instructions),
        _CardMarker("creator_notes", card.creator_notes),
    ]
    markers.extend(_CardMarker(f"alternate_greeting[{index}]", text) for index, text in enumerate(card.alternate_greetings))
    markers.extend(_CardMarker(f"group_only_greeting[{index}]", text) for index, text in enumerate(card.group_only_greetings))
    markers.extend(_CardMarker(f"lorebook[{index}]", entry.content) for index, entry in enumerate(card.lorebook))
    return [marker for marker in markers if len(_normalized_body(marker.text)) >= 16]


def _card_marker_segments(marker: _CardMarker) -> list[str]:
    normalized = _normalized_body(marker.text)
    if len(normalized) < 16:
        return []
    segments = {normalized}
    for sentence in re.split(r"(?:[.!?。！？]+|\n+)", marker.text):
        normalized_sentence = _normalized_body(sentence)
        if len(normalized_sentence) >= 32:
            segments.add(normalized_sentence)
    return sorted(segments, key=len, reverse=True)


def _active_persona_cards(meeting_dir: Path, meeting: dict[str, object]) -> tuple[list[PersonaCard], list[str], list[dict[str, object]]]:
    output_root = meeting_dir.parent.parent
    character_mode = meeting.get("character_mode") if isinstance(meeting.get("character_mode"), dict) else {}
    cards: list[PersonaCard] = []
    ignored_features: list[str] = []
    card_issues: list[dict[str, object]] = []
    for agent in character_mode.get("agents") if isinstance(character_mode.get("agents"), list) else []:
        if not isinstance(agent, dict):
            continue
        if str(agent.get("mode") or "off") == "off":
            continue
        ignored = agent.get("ignored_features") if isinstance(agent.get("ignored_features"), dict) else {}
        ignored_features.extend(str(name) for name in ignored)
        card_path = _active_card_path(output_root, agent)
        if card_path is None:
            card_issues.append(_card_issue(agent))
            continue
        try:
            cards.append(load_persona_card(card_path))
        except (OSError, ValueError, json.JSONDecodeError):
            card_issues.append(_card_issue(agent))
            continue
    return cards, ignored_features, card_issues


def _card_issue(agent: dict[str, object]) -> dict[str, object]:
    return {
        "code": "persona_card_unavailable",
        "count": 1,
        "agent_id": _safe_report_token(agent.get("agent_id"), limit=64),
        "card_id": _safe_report_token(agent.get("card_id"), limit=80),
    }


def _active_card_path(output_root: Path, agent: dict[str, object]) -> Path | None:
    source_path = str(agent.get("source_path") or "").strip()
    if source_path:
        path = (output_root / source_path).resolve(strict=False)
        try:
            path.relative_to(output_root.resolve(strict=False))
        except ValueError:
            return None
        return path
    card_id = str(agent.get("card_id") or "").strip()
    if not card_id or any(separator in card_id for separator in {"/", "\\"}):
        return None
    return output_root / "personas" / card_id / "card.json"


def _artifact_files(meeting_dir: Path) -> list[tuple[Path, str]]:
    paths: list[tuple[Path, str]] = []
    for name, surface in (
        ("transcript.md", "transcript"),
        ("decision.md", "decision"),
    ):
        path = meeting_dir / name
        if path.exists():
            paths.append((path, surface))
    paths.extend((path, "task") for path in sorted((meeting_dir / "tasks").glob("*.md")))
    paths.extend((path, "delegate_packet") for path in sorted((meeting_dir / "delegate_packets").glob("*.md")))
    paths.extend((path, "json_artifact") for path in sorted((meeting_dir / "delegate_packets").glob("*.json")))
    paths.extend((path, "return_packet") for path in sorted((meeting_dir / "return_packets").glob("*.md")))
    paths.extend((path, "json_artifact") for path in sorted((meeting_dir / "return_packets").glob("*.json")))
    shared_dir = meeting_dir / "shared_memory"
    paths.extend((path, "shared_memory") for path in sorted(shared_dir.glob("*.md")))
    paths.extend((path, "json_artifact") for path in sorted(shared_dir.glob("*.json")))
    return paths


def _artifact_relative_path(meeting_dir: Path, path: Path) -> str:
    try:
        return str(path.relative_to(meeting_dir))
    except ValueError:
        return path.name


def _safe_surface(surface: str) -> str:
    surface_id = str(surface or "").strip()
    if surface_id in ARTIFACT_SURFACES or surface_id in SPEECH_SURFACES:
        return surface_id
    return "artifact"


def _normalized_body(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def _safe_report_token(value: object, *, limit: int) -> str:
    text = re.sub(r"[^A-Za-z0-9_.:-]+", "-", str(value or "").strip()).strip(".-")
    return text[:limit]
