from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Mapping

from agentsassemble.room.text import clean_room_text as clean_lobby_text
from agentsassemble.room_attention import AttentionEvaluation
from agentsassemble.room_engagement import message_mentions_all


SHADOW_ATTENTION_MODES = frozenset({"off", "sample", "full"})
SHADOW_ATTENTION_SAMPLE_MODULUS = 16
AMBIENT_TEXT_MESSAGE_KINDS = frozenset({"", "message", "text"})


def normalize_shadow_attention_mode(value: object) -> str:
    mode = clean_lobby_text(value, limit=16).lower() or "off"
    if mode not in SHADOW_ATTENTION_MODES:
        raise ValueError(f"Unsupported attention shadow mode: {mode}")
    return mode


def should_record_shadow_attention(event: dict[str, object], mode: object) -> bool:
    clean_mode = normalize_shadow_attention_mode(mode)
    if clean_mode == "full":
        return True
    if clean_mode == "off":
        return False
    source_seq = max(0, int(event.get("seq") or 0))
    return source_seq > 0 and source_seq % SHADOW_ATTENTION_SAMPLE_MODULUS == 0


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

    if message_mentions_all(content):
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


def evaluate_ambient_attention(
    event: dict[str, object],
    *,
    candidate_ids: Iterable[str],
    eligible_ids: Iterable[str],
    last_spoke_sequences: Mapping[str, int],
) -> AttentionEvaluation:
    """Select at most one provider for an opt-in ambient room event."""

    actor = event.get("actor") if isinstance(event.get("actor"), dict) else {}
    actor_id = clean_lobby_text(
        actor.get("participant_id") or event.get("participant_id"),
        limit=128,
    )
    actor_type = clean_lobby_text(
        actor.get("participant_type") or event.get("participant_type"),
        limit=32,
    )
    candidates = _clean_ids(candidate_ids, exclude=actor_id)
    eligible = _clean_ids(eligible_ids, exclude=actor_id)
    base = evaluate_attention(
        event,
        candidate_ids=candidates,
        eligible_ids=eligible,
    )
    rejection_reason = ambient_trigger_rejection_reason(event)
    if rejection_reason:
        return AttentionEvaluation(
            room_id=base.room_id,
            source_event_id=base.source_event_id,
            source_seq=base.source_seq,
            outcome="silent",
            reasons=(rejection_reason,),
        )
    if base.outcome == "selected":
        return base
    if "explicit_target_unavailable" in base.reasons:
        return base

    selectable = base.eligible_participant_ids
    reasons = base.reasons
    eligible_set = set(eligible)
    if not selectable and actor_type != "agent" and "no_attention_signal" in base.reasons:
        selectable = tuple(candidate for candidate in candidates if candidate in eligible_set)
        reasons = ("ambient_human_message",)
    elif not selectable and actor_type == "agent" and "agent_message_no_direct_signal" in base.reasons:
        selectable = tuple(candidate for candidate in candidates if candidate in eligible_set)
        reasons = ("ambient_agent_handoff",)
    if not selectable:
        return base

    selected = min(
        selectable,
        key=lambda participant_id: (
            max(0, int(last_spoke_sequences.get(participant_id, 0))),
            participant_id,
        ),
    )
    return AttentionEvaluation(
        room_id=base.room_id,
        source_event_id=base.source_event_id,
        source_seq=base.source_seq,
        outcome="selected",
        selected_participant_id=selected,
        eligible_participant_ids=tuple(selectable),
        reasons=(*reasons, "ambient_fair_selection"),
    )


def ambient_trigger_rejection_reason(event: dict[str, object]) -> str:
    """Return why a committed event cannot wake an ambient room provider."""

    if clean_lobby_text(event.get("type"), limit=64) != "message_final":
        return "ambient_event_type_not_supported"
    message_kind = clean_lobby_text(event.get("message_kind"), limit=64).lower()
    if message_kind in {"vote", "vote_cast", "vote_withdraw"}:
        return "ambient_vote_event"
    if message_kind not in AMBIENT_TEXT_MESSAGE_KINDS:
        return "ambient_message_kind_not_supported"
    content = clean_lobby_text(event.get("content"), limit=12000)
    if not content:
        attachments = event.get("attachments") if isinstance(event.get("attachments"), list) else []
        return "ambient_unsupported_media_only" if attachments else "ambient_empty_content"
    actor = event.get("actor") if isinstance(event.get("actor"), dict) else {}
    actor_type = clean_lobby_text(
        actor.get("participant_type") or event.get("participant_type"),
        limit=32,
    )
    if actor_type in {"human", "agent"}:
        return ""
    metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
    if event.get("trusted_ambient_trigger") is True or metadata.get("trusted_ambient_trigger") is True:
        return ""
    return "ambient_actor_not_trusted"


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


def _looks_like_room_question(content: str) -> bool:
    return content.rstrip().endswith(("?", "？"))


def _clean_ids(values: Iterable[object], *, exclude: str = "") -> tuple[str, ...]:
    return tuple(dict.fromkeys(
        value
        for value in (clean_lobby_text(item, limit=128) for item in values)
        if value and value != exclude
    ))
