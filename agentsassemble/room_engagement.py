"""Shared participant-engagement rules for every room participation path.

LiveAgentRunner (managed residents) and mcp_server (tool-loop participants)
previously carried near-identical copies of these predicates; a fix applied to
one silently diverged from the other. This module is the single source of
truth for: event ordering after a cursor, chain depth, self/human detection,
mention matching (including owner-prefixed display names and Korean particle
suffixes), and the engagement-mode reply decision.
"""
from __future__ import annotations

import re

# Leading/trailing boundary excludes ASCII word chars and dashes but allows
# Hangul to follow, so Korean particles attached to a name still mention it
# ("페이블찡은 천재" mentions 페이블찡). Substrings of longer ASCII tokens stay
# excluded ("haiku" does not match inside "haiku-duo").
_ASCII_BOUNDARY_BEFORE = r"(?<![A-Za-z0-9_-])"
_ASCII_BOUNDARY_AFTER = r"(?![A-Za-z0-9_-])"

# "SeiNel's 페이블" / "ㅁㅁ’s 에이전트" / "정지훈의 페이블" — strip the owner
# prefix so the bare agent name still works as a mention.
_POSSESSIVE_PREFIX_RE = re.compile(r"^.{1,64}?(?:['’]s\s+|의\s+)(?=\S)")


def events_after(events: list[dict[str, object]], last_observed_event_id: str) -> list[dict[str, object]]:
    if not last_observed_event_id:
        return events
    for index, event in enumerate(events):
        if event.get("id") == last_observed_event_id:
            return events[index + 1 :]
    return events


def chain_depth(event: dict[str, object]) -> int:
    value = event.get("auto_chain_depth")
    if isinstance(value, int) and not isinstance(value, bool):
        return max(0, value)
    return 0


def is_self_event(event: dict[str, object], agent_id: str, display_name: str) -> bool:
    actor_id = str(event.get("actor_id") or "")
    if actor_id and actor_id == agent_id:
        return True
    return bool(display_name) and str(event.get("name") or "") == display_name


def is_human_lobby_event(event: dict[str, object]) -> bool:
    """True when the event was authored by a person.

    The server stamps `actor_type` on append (identity layer); that wins.
    Events recorded before the stamp existed fall back to the historical
    heuristic: no actor_id means the host-browser human path.
    """
    actor_type = str(event.get("actor_type") or "").strip().lower()
    if actor_type == "human":
        return True
    if actor_type == "agent":
        return False
    if str(event.get("actor_id") or ""):
        return False
    side = str(event.get("side") or "")
    return side in {"", "mine", "other"}


def mention_aliases(agent_id: str, display_name: str) -> list[str]:
    """All names that should count as mentioning this agent.

    Includes the bare agent name when the display name carries an owner
    prefix ("X's name" / "X의 name"), so panels can show ownership without
    breaking mentions.
    """
    aliases: list[str] = []
    seen: set[str] = set()
    for raw in (agent_id, display_name, _POSSESSIVE_PREFIX_RE.sub("", str(display_name or ""), count=1)):
        cleaned = str(raw or "").strip()
        key = cleaned.casefold()
        if len(cleaned) < 2 or key in seen:
            continue
        seen.add(key)
        aliases.append(cleaned)
    return aliases


def message_mentions_agent(message: str, agent_id: str, display_name: str) -> bool:
    normalized_message = str(message or "").casefold()
    return any(
        _contains_mention_token(normalized_message, alias.casefold())
        for alias in mention_aliases(agent_id, display_name)
    )


def message_directly_mentions_agent(message: str, agent_id: str, display_name: str) -> bool:
    normalized_message = str(message or "").casefold()
    return any(
        _contains_direct_mention_token(normalized_message, alias.casefold())
        for alias in mention_aliases(agent_id, display_name)
    )


def _contains_mention_token(normalized_message: str, normalized_mention: str) -> bool:
    mention = normalized_mention.strip()
    if not mention:
        return False
    pattern = rf"{_ASCII_BOUNDARY_BEFORE}{re.escape(mention)}{_ASCII_BOUNDARY_AFTER}"
    return re.search(pattern, normalized_message) is not None


def _contains_direct_mention_token(normalized_message: str, normalized_mention: str) -> bool:
    mention = normalized_mention.strip()
    if not mention:
        return False
    token = re.escape(mention)
    at_pattern = rf"{_ASCII_BOUNDARY_BEFORE}@{token}{_ASCII_BOUNDARY_AFTER}"
    angle_pattern = rf"<@\s*{token}\s*>"
    return re.search(at_pattern, normalized_message) is not None or re.search(angle_pattern, normalized_message) is not None


def resolve_engagement(conversation_mode: str, agent_engagement_mode: str) -> str:
    """Map the room's mode to how eagerly an agent reacts.

    quiet   → "mentioned" (speak only when @called) — the token-cheap default.
    free    → "always" (react to everything, no floor).
    ordered → "always" too — every agent *wants* to react; the floor algorithm
              (see room_uses_floor / should_yield_for_floor) is what spaces them
              out, not the engagement mode.
    Unknown modes fall back to the agent's own engagement_mode (legacy/advanced)."""
    mode = str(conversation_mode or "").strip().lower()
    if mode in {"free", "ordered"}:
        return "always"
    if mode == "quiet":
        return "mentioned"
    return str(agent_engagement_mode or "mentioned")


def room_uses_floor(conversation_mode: str) -> bool:
    """True for "ordered": a deterministic turn floor spaces speakers out."""
    return str(conversation_mode or "").strip().lower() == "ordered"


def should_yield_for_floor(
    events: list[dict[str, object]],
    agent_id: str,
    display_name: str,
    *,
    recent_window: int = 8,
) -> bool:
    """Deterministic turn floor for "ordered" rooms — no AI decides turns, this does.

    An agent yields (stays silent this round) when, among the recent human+agent
    messages, it has already spoken at least as much as the quietest active
    speaker — i.e. it's not "behind". It also never speaks twice in a row. The
    effect is rough round-robin: after you speak you're ahead, so you yield until
    peers catch up, then you're behind again and may speak. Humans are never
    counted (they always have priority), so a human message always lets agents
    respond."""
    me = str(agent_id or "")
    recent = [
        event
        for event in events
        if str(event.get("kind") or "message") == "message"
        and str(event.get("message") or "").strip()
    ][-max(1, int(recent_window)):]
    if not recent:
        return False
    # No double-speak: if the last message was mine, yield.
    last = recent[-1]
    if is_self_event(last, me, display_name):
        return True
    # Count only agent speakers (humans always have priority, never gate agents).
    counts: dict[str, int] = {}
    for event in recent:
        if is_human_lobby_event(event):
            continue
        speaker = str(event.get("actor_id") or event.get("name") or "")
        if speaker:
            counts[speaker] = counts.get(speaker, 0) + 1
    my_count = counts.get(me, 0) or counts.get(display_name, 0)
    if not counts or my_count == 0:
        return False  # I haven't spoken recently → I'm behind → speak.
    # Yield while I'm not strictly the most-behind active speaker.
    return my_count > min(counts.values())


def should_reply_to_event(
    engagement_mode: str,
    event: dict[str, object],
    agent_id: str,
    display_name: str,
) -> bool:
    mode = str(engagement_mode or "mentioned").strip().lower().replace("-", "_")
    if str(event.get("kind") or "") == "vote_cast":
        return False  # ballots are markers, not conversation — never chat-reply to them
    if mode == "always":
        return True
    if mode in {"watch", "manual", "moderator_called"}:
        return False
    if mode == "human_only":
        return is_human_lobby_event(event)
    if mode == "flow":
        return is_human_lobby_event(event) or message_mentions_agent(str(event.get("message") or ""), agent_id, display_name)
    return message_mentions_agent(str(event.get("message") or ""), agent_id, display_name)
