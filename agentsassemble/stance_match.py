from __future__ import annotations

import re


def position_matches_winner(position: str, winner: str) -> bool:
    if not position or not winner:
        return False
    normalized_position = position.casefold()
    for term in winner_terms(winner):
        if _contains_opposition_marker(normalized_position, term):
            return False
        if _positive_match(normalized_position, term):
            return True
    return False


def position_opposes_winner(position: str, winner: str) -> bool:
    if not position or not winner:
        return False
    normalized_position = position.casefold()
    return any(_contains_opposition_marker(normalized_position, term) for term in winner_terms(winner))


def winner_terms(winner: str) -> set[str]:
    normalized_winner = winner.casefold().strip()
    split_terms = {
        term
        for term in normalized_winner.replace("/", " ").split()
        if term and _is_specific_term(term)
    }
    terms = set(split_terms)
    if normalized_winner:
        terms.add(normalized_winner)
    terms.update(_winner_aliases(normalized_winner))
    return terms


def _positive_match(position: str, term: str) -> bool:
    if not term:
        return False
    if position == term or _starts_with_term(position, term):
        return True
    positive_patterns = (
        f"choose {term}",
        f"pick {term}",
        f"select {term}",
        f"support {term}",
        f"winner is {term}",
        f"결론은 {term}",
        f"{term} wins",
        f"{term} win",
        f"{term} 우세",
        f"{term} 승",
        f"{term} 승리",
        f"{term} 쪽",
    )
    return any(_contains_phrase(position, phrase) for phrase in positive_patterns)


def _starts_with_term(position: str, term: str) -> bool:
    if not position.startswith(term):
        return False
    if len(position) == len(term):
        return True
    next_char = position[len(term)]
    return not (next_char.isascii() and (next_char.isalnum() or next_char == "_"))


def _contains_phrase(position: str, phrase: str) -> bool:
    if phrase.isascii():
        return re.search(rf"(?<![a-z0-9_]){re.escape(phrase)}(?![a-z0-9_])", position) is not None
    return phrase in position


def _is_specific_term(term: str) -> bool:
    if len(term) <= 1:
        return False
    generic_terms = {"option", "team", "side", "candidate", "choice", "answer", "입장", "선택지"}
    return term not in generic_terms


def _contains_opposition_marker(position: str, winner_term: str) -> bool:
    markers = (
        f"not {winner_term}",
        f"against {winner_term}",
        f"beats {winner_term}",
        f"beat {winner_term}",
        f"defeats {winner_term}",
        f"defeat {winner_term}",
        f"{winner_term} loses",
        f"{winner_term} lose",
        f"{winner_term} is not",
        f"{winner_term} 아님",
        f"{winner_term} 반대",
    )
    return any(marker in position for marker in markers)


def _winner_aliases(winner: str) -> set[str]:
    aliases = {
        "akainu": {"아카이누", "사카즈키", "sakazuki"},
        "sakazuki": {"아카이누", "akainu"},
        "aokiji": {"아오키지", "쿠잔", "kuzan"},
        "kuzan": {"아오키지", "aokiji"},
        "kizaru": {"키자루", "보르살리노", "borsalino"},
        "borsalino": {"키자루", "kizaru"},
    }
    result = set()
    for key, values in aliases.items():
        if key in winner:
            result.update(value.casefold() for value in values)
    return result
