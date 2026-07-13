from __future__ import annotations

import re
from collections.abc import Iterable

from agentsassemble.meeting_events import clean_lobby_text
from agentsassemble.room_attention import AttentionEvaluation


def evaluate_attention(
    event: dict[str, object],
    *,
    candidate_ids: Iterable[str],
    eligible_ids: Iterable[str],
) -> AttentionEvaluation:
    room_id = clean_lobby_text(event.get("room_id"), limit=128)
    source_event_id = clean_lobby_text(event.get("id"), limit=128)
    source_seq = int(event.get("seq") or 0)
    actor = event.get("actor") if isinstance(event.get("actor"), dict) else {}
    actor_id = clean_lobby_text(actor.get("participant_id") or event.get("participant_id"), limit=128)
    actor_type = clean_lobby_text(actor.get("participant_type") or event.get("participant_type"), limit=32)
    content = clean_lobby_text(event.get("content"), limit=12000)
    candidates = _clean_ids(candidate_ids, exclude=actor_id)
    eligible_set = set(_clean_ids(eligible_ids))
    eligible = tuple(candidate for candidate in candidates if candidate in eligible_set)
    eligible_set = set(eligible)

    explicit_targets, reasons = _explicit_targets(event, content, candidates)
    if explicit_targets:
        available_targets = tuple(candidate for candidate in explicit_targets if candidate in eligible_set)
        if len(available_targets) == 1:
            return AttentionEvaluation(
                room_id=room_id,
                source_event_id=source_event_id,
                source_seq=source_seq,
                outcome="selected",
                selected_participant_id=available_targets[0],
                eligible_participant_ids=available_targets,
                reasons=reasons,
            )
        if available_targets:
            return AttentionEvaluation(
                room_id=room_id,
                source_event_id=source_event_id,
                source_seq=source_seq,
                outcome="eligible",
                eligible_participant_ids=available_targets,
                reasons=reasons,
            )
        return AttentionEvaluation(
            room_id=room_id,
            source_event_id=source_event_id,
            source_seq=source_seq,
            outcome="silent",
            reasons=(*reasons, "explicit_target_unavailable"),
        )

    if _contains_all_mention(content):
        if eligible:
            return AttentionEvaluation(
                room_id=room_id,
                source_event_id=source_event_id,
                source_seq=source_seq,
                outcome="eligible",
                eligible_participant_ids=eligible,
                reasons=("room_broadcast",),
            )
        return AttentionEvaluation(
            room_id=room_id,
            source_event_id=source_event_id,
            source_seq=source_seq,
            outcome="silent",
            reasons=("room_broadcast", "no_eligible_candidate"),
        )

    if actor_type != "agent" and _looks_like_room_question(content):
        if eligible:
            return AttentionEvaluation(
                room_id=room_id,
                source_event_id=source_event_id,
                source_seq=source_seq,
                outcome="eligible",
                eligible_participant_ids=eligible,
                reasons=("room_question",),
            )
        return AttentionEvaluation(
            room_id=room_id,
            source_event_id=source_event_id,
            source_seq=source_seq,
            outcome="silent",
            reasons=("room_question", "no_eligible_candidate"),
        )

    reason = "agent_message_no_direct_signal" if actor_type == "agent" else "no_attention_signal"
    if not candidates:
        reason = "no_candidate"
    return AttentionEvaluation(
        room_id=room_id,
        source_event_id=source_event_id,
        source_seq=source_seq,
        outcome="silent",
        reasons=(reason,),
    )


def _explicit_targets(
    event: dict[str, object],
    content: str,
    candidates: tuple[str, ...],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    candidate_set = set(candidates)
    metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
    field_targets = _clean_ids(
        (
            event.get("target_agent_id"),
            event.get("reply_to_participant_id"),
            event.get("invite_next_participant_id"),
            metadata.get("reply_to_participant_id"),
            metadata.get("invite_next_participant_id"),
        )
    )
    field_targets = tuple(target for target in field_targets if target in candidate_set)
    reasons: list[str] = []
    if field_targets:
        if clean_lobby_text(event.get("target_agent_id"), limit=128) in field_targets:
            reasons.append("explicit_target")
        if any("reply_to" in key and clean_lobby_text(value, limit=128) in field_targets for key, value in (
            ("reply_to_participant_id", event.get("reply_to_participant_id")),
            ("metadata_reply_to", metadata.get("reply_to_participant_id")),
        )):
            reasons.append("direct_reply")
        if any(clean_lobby_text(value, limit=128) in field_targets for value in (
            event.get("invite_next_participant_id"),
            metadata.get("invite_next_participant_id"),
        )):
            reasons.append("invite_next")

    mentioned = tuple(
        candidate
        for candidate in candidates
        if re.search(rf"(?<![\w-])@{re.escape(candidate.casefold())}(?![\w-])", content.casefold())
    )
    if mentioned:
        reasons.append("direct_mention")
    return tuple(dict.fromkeys((*field_targets, *mentioned))), tuple(dict.fromkeys(reasons))


def _contains_all_mention(content: str) -> bool:
    return bool(re.search(r"(?<![\w-])@all(?![\w-])", content.casefold()))


def _looks_like_room_question(content: str) -> bool:
    return content.rstrip().endswith(("?", "？"))


def _clean_ids(values: Iterable[object], *, exclude: str = "") -> tuple[str, ...]:
    return tuple(dict.fromkeys(
        value
        for value in (clean_lobby_text(item, limit=128) for item in values)
        if value and value != exclude
    ))
