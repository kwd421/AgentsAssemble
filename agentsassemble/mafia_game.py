from __future__ import annotations

import hashlib
import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from agentsassemble.meeting_events import clean_lobby_text


MAFIA_GAME_LOCK = threading.RLock()
MAFIA_CHANNELS = {"all", "mafia_team"}
MAFIA_PHASES = {"day", "night", "ended"}


def start_mafia_game(output_root: Path, payload: dict[str, object]) -> dict[str, object]:
    game_id = _game_id(payload.get("game_id") or "mafia-night")
    players = _players(payload.get("players"))
    mafia_count = _mafia_count(payload.get("mafia_count"), player_count=len(players))
    now = _now()
    game = {
        "game_id": game_id,
        "status": "running",
        "phase": "day",
        "day_number": 1,
        "created_at": now,
        "updated_at": now,
        "winner": "",
        "players": _assign_roles(game_id, players, mafia_count),
        "events": [],
        "votes": {},
    }
    _append_event(game, "system", "all", "system", "마피아 게임이 시작되었습니다.")
    _save_game(output_root, game)
    return game


def mafia_game_payload(output_root: Path, game_id: str, *, viewer_agent_id: str = "") -> dict[str, object]:
    game = _read_game(output_root, game_id)
    return _visible_game(game, viewer_agent_id=viewer_agent_id)


def post_mafia_chat(output_root: Path, payload: dict[str, object]) -> dict[str, object]:
    with MAFIA_GAME_LOCK:
        game = _read_game(output_root, _game_id(payload.get("game_id")))
        if game.get("phase") == "ended":
            raise ValueError("Mafia game has ended.")
        speaker_id = _agent_id(payload.get("speaker_id"))
        speaker = _player(game, speaker_id)
        if not speaker.get("alive"):
            raise ValueError("Dead players cannot speak.")
        channel = _channel(payload.get("channel"))
        if channel == "mafia_team" and speaker.get("team") != "mafia":
            raise ValueError("Only mafia players can use mafia team chat.")
        event = _append_event(
            game,
            "chat",
            channel,
            speaker_id,
            clean_lobby_text(payload.get("message", ""), limit=1000),
        )
        game["updated_at"] = _now()
        _save_game(output_root, game)
        return event


def cast_mafia_vote(output_root: Path, payload: dict[str, object]) -> dict[str, object]:
    with MAFIA_GAME_LOCK:
        game = _read_game(output_root, _game_id(payload.get("game_id")))
        if game.get("phase") == "ended":
            raise ValueError("Mafia game has ended.")
        voter_id = _agent_id(payload.get("voter_id"))
        target_id = _agent_id(payload.get("target_id"))
        voter = _player(game, voter_id)
        target = _player(game, target_id)
        if not voter.get("alive") or not target.get("alive"):
            raise ValueError("Votes require living players.")
        phase = str(game.get("phase") or "day")
        if phase == "night" and voter.get("team") != "mafia":
            raise ValueError("Only mafia players can vote at night.")
        votes = game.setdefault("votes", {})
        if not isinstance(votes, dict):
            votes = {}
            game["votes"] = votes
        phase_votes = votes.setdefault(phase, {})
        if not isinstance(phase_votes, dict):
            phase_votes = {}
            votes[phase] = phase_votes
        phase_votes[voter_id] = target_id
        event_channel = "mafia_team" if phase == "night" else "all"
        event = _append_event(game, "vote", event_channel, voter_id, f"{_display_name(voter)} → {_display_name(target)}")
        game["updated_at"] = _now()
        _save_game(output_root, game)
        return event


def resolve_mafia_phase(output_root: Path, payload: dict[str, object]) -> dict[str, object]:
    with MAFIA_GAME_LOCK:
        game = _read_game(output_root, _game_id(payload.get("game_id")))
        if game.get("phase") == "ended":
            return game
        phase = str(game.get("phase") or "day")
        if phase not in MAFIA_PHASES:
            raise ValueError("Unknown mafia phase.")
        target_id = _vote_target(game, phase)
        if target_id:
            target = _player(game, target_id)
            target["alive"] = False
            if phase == "night":
                message = f"밤이 끝났습니다. {_display_name(target)}가 사망했습니다."
            else:
                message = f"투표 결과 {_display_name(target)}가 추방되었습니다."
            _append_event(game, "system", "all", "system", message)
        else:
            _append_event(game, "system", "all", "system", "투표가 모이지 않아 아무 일도 일어나지 않았습니다.")
        winner = _winner(game)
        if winner:
            game["phase"] = "ended"
            game["status"] = "finished"
            game["winner"] = winner
            _append_event(game, "system", "all", "system", f"{_winner_label(winner)} 승리.")
        elif phase == "day":
            game["phase"] = "night"
            _append_event(game, "system", "all", "system", "밤이 되었습니다. 마피아는 팀채팅으로 의논할 수 있습니다.")
        else:
            game["phase"] = "day"
            game["day_number"] = int(game.get("day_number") or 1) + 1
            _append_event(game, "system", "all", "system", f"{game['day_number']}일차 낮이 시작되었습니다.")
        votes = game.setdefault("votes", {})
        if isinstance(votes, dict):
            votes[phase] = {}
        game["updated_at"] = _now()
        _save_game(output_root, game)
        return game


def _visible_game(game: dict[str, object], *, viewer_agent_id: str) -> dict[str, object]:
    viewer = clean_lobby_text(viewer_agent_id, limit=64)
    viewer_role = ""
    viewer_team = ""
    host_view = viewer in {"host", "owner"}
    players = [dict(player) for player in _player_list(game)]
    for player in players:
        if player.get("agent_id") == viewer:
            viewer_role = str(player.get("role") or "")
            viewer_team = str(player.get("team") or "")
    visible_players = []
    for player in players:
        item = {
            "agent_id": player.get("agent_id", ""),
            "display_name": player.get("display_name", ""),
            "alive": player.get("alive") is True,
        }
        if host_view or player.get("agent_id") == viewer:
            item["role"] = player.get("role", "")
            item["team"] = player.get("team", "")
        visible_players.append(item)
    visible_events = [
        event
        for event in _event_list(game)
        if _event_visible(event, viewer=viewer, viewer_team=viewer_team, host_view=host_view)
    ]
    payload = dict(game)
    payload["players"] = visible_players
    payload["events"] = visible_events
    if not host_view:
        payload.pop("votes", None)
    if viewer and not host_view:
        payload["viewer"] = {"agent_id": viewer, "role": viewer_role, "team": viewer_team}
    return payload


def _event_visible(event: dict[str, object], *, viewer: str, viewer_team: str, host_view: bool) -> bool:
    channel = str(event.get("channel") or "all")
    if channel == "all":
        return True
    if channel == "mafia_team":
        return host_view or viewer_team == "mafia"
    return host_view


def _assign_roles(game_id: str, players: list[dict[str, object]], mafia_count: int) -> list[dict[str, object]]:
    ranked = sorted(
        players,
        key=lambda player: hashlib.sha256(f"{game_id}:{player['agent_id']}".encode("utf-8")).hexdigest(),
    )
    mafia_ids = {str(player["agent_id"]) for player in ranked[:mafia_count]}
    assigned = []
    for player in players:
        role = "mafia" if player["agent_id"] in mafia_ids else "town"
        assigned.append({**player, "role": role, "team": "mafia" if role == "mafia" else "town", "alive": True})
    return assigned


def _players(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise ValueError("Mafia game requires players.")
    players = []
    seen = set()
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            continue
        agent_id = _agent_id(item.get("agent_id") or item.get("id") or f"player-{index + 1}")
        if agent_id in seen:
            raise ValueError(f"Duplicate mafia player id: {agent_id}")
        seen.add(agent_id)
        players.append(
            {
                "agent_id": agent_id,
                "display_name": clean_lobby_text(item.get("display_name") or item.get("name") or agent_id, limit=80) or agent_id,
            }
        )
    if len(players) < 3:
        raise ValueError("Mafia game requires at least 3 players.")
    return players[:16]


def _mafia_count(value: object, *, player_count: int) -> int:
    try:
        count = int(value)
    except (TypeError, ValueError):
        count = max(1, player_count // 4)
    return min(max(1, count), max(1, player_count - 2))


def _vote_target(game: dict[str, object], phase: str) -> str:
    votes = game.get("votes") if isinstance(game.get("votes"), dict) else {}
    phase_votes = votes.get(phase) if isinstance(votes, dict) and isinstance(votes.get(phase), dict) else {}
    counts: dict[str, int] = {}
    for target_id in phase_votes.values():
        target = str(target_id or "")
        if target:
            counts[target] = counts.get(target, 0) + 1
    if not counts:
        return ""
    winner, count = max(counts.items(), key=lambda item: (item[1], item[0]))
    if list(counts.values()).count(count) > 1:
        return ""
    return winner


def _winner(game: dict[str, object]) -> str:
    alive = [player for player in _player_list(game) if player.get("alive") is True]
    mafia = [player for player in alive if player.get("team") == "mafia"]
    town = [player for player in alive if player.get("team") != "mafia"]
    if not mafia:
        return "town"
    if len(mafia) >= len(town):
        return "mafia"
    return ""


def _append_event(game: dict[str, object], kind: str, channel: str, actor_id: str, message: str) -> dict[str, object]:
    event = {
        "id": uuid4().hex[:12],
        "created_at": _now(),
        "kind": kind,
        "channel": channel,
        "actor_id": actor_id,
        "name": _actor_name(game, actor_id),
        "message": clean_lobby_text(message, limit=1000),
        "day_number": game.get("day_number") or 1,
        "phase": game.get("phase") or "day",
    }
    events = game.setdefault("events", [])
    if not isinstance(events, list):
        events = []
        game["events"] = events
    events.append(event)
    return event


def _read_game(output_root: Path, game_id: str) -> dict[str, object]:
    path = _game_path(output_root, _game_id(game_id))
    if not path.exists():
        raise ValueError("Mafia game was not found.")
    try:
        game = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError("Mafia game is unreadable.") from error
    if not isinstance(game, dict):
        raise ValueError("Mafia game is unreadable.")
    return game


def _save_game(output_root: Path, game: dict[str, object]) -> None:
    path = _game_path(output_root, _game_id(game.get("game_id")))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(game, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _game_path(output_root: Path, game_id: str) -> Path:
    return output_root / "play" / "mafia" / f"{game_id}.json"


def _game_id(value: object) -> str:
    game_id = clean_lobby_text(value, limit=128)
    if not game_id or "/" in game_id or "\\" in game_id or Path(game_id).name != game_id or game_id in {".", ".."}:
        raise ValueError("Invalid mafia game id.")
    return game_id


def _agent_id(value: object) -> str:
    agent_id = clean_lobby_text(value, limit=64)
    if not agent_id:
        raise ValueError("Agent id is required.")
    return agent_id


def _channel(value: object) -> str:
    channel = clean_lobby_text(value or "all", limit=32)
    if channel not in MAFIA_CHANNELS:
        raise ValueError("Invalid mafia chat channel.")
    return channel


def _player(game: dict[str, object], agent_id: str) -> dict[str, object]:
    for player in _player_list(game):
        if player.get("agent_id") == agent_id:
            return player
    raise ValueError("Mafia player was not found.")


def _player_list(game: dict[str, object]) -> list[dict[str, object]]:
    players = game.get("players")
    return [player for player in players if isinstance(player, dict)] if isinstance(players, list) else []


def _event_list(game: dict[str, object]) -> list[dict[str, object]]:
    events = game.get("events")
    return [event for event in events if isinstance(event, dict)] if isinstance(events, list) else []


def _actor_name(game: dict[str, object], actor_id: str) -> str:
    if actor_id == "system":
        return "Mafia System"
    for player in _player_list(game):
        if player.get("agent_id") == actor_id:
            return _display_name(player)
    return actor_id


def _display_name(player: dict[str, object]) -> str:
    return str(player.get("display_name") or player.get("agent_id") or "player")


def _winner_label(winner: str) -> str:
    return "마피아" if winner == "mafia" else "시민"


def _now() -> str:
    return datetime.now(UTC).isoformat()
