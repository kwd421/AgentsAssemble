"""Lore selection and provider prompt rendering for persona cards."""

from __future__ import annotations

import hashlib
import re
from dataclasses import replace
from typing import Any

from agentsassemble.persona_cards.models import (
    PersonaCard,
    PersonaLoreEntry,
    PersonaLoreScanResult,
    PersonaPromptRender,
    _safe_speech_style,
)
from agentsassemble.persona_cards.values import _int, _prompt_text, _text


MAX_LORE_RECURSIVE_SCAN_DEPTH = 8


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
