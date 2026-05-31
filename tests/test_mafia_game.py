import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from http.server import ThreadingHTTPServer

from agentsassemble.gui import _make_handler
from agentsassemble.mafia_game import (
    cast_mafia_vote,
    mafia_game_payload,
    post_mafia_chat,
    resolve_mafia_phase,
    start_mafia_game,
    submit_mafia_action,
)


PLAYERS = [
    {"agent_id": "spark-a", "display_name": "Codex Spark A"},
    {"agent_id": "spark-b", "display_name": "Codex Spark B"},
    {"agent_id": "spark-c", "display_name": "Codex Spark C"},
    {"agent_id": "spark-d", "display_name": "Codex Spark D"},
]

CLASSIC_PLAYERS = [
    {"agent_id": "mafia-a", "display_name": "Mafia A", "role": "mafia"},
    {"agent_id": "doctor-a", "display_name": "Doctor A", "role": "doctor"},
    {"agent_id": "detective-a", "display_name": "Detective A", "role": "detective"},
    {"agent_id": "town-a", "display_name": "Town A", "role": "town"},
    {"agent_id": "town-b", "display_name": "Town B", "role": "town"},
]


class MafiaGameTests(unittest.TestCase):
    def test_mafia_team_chat_is_visible_only_to_mafia_and_host(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            game = start_mafia_game(root, {"game_id": "mafia-room", "players": PLAYERS, "mafia_count": 1})
            mafia_id = _mafia_ids(game)[0]
            town_id = next(player["agent_id"] for player in game["players"] if player["agent_id"] != mafia_id)

            post_mafia_chat(
                root,
                {"game_id": "mafia-room", "speaker_id": "spark-a", "channel": "all", "message": "전체 채팅입니다."},
            )
            post_mafia_chat(
                root,
                {"game_id": "mafia-room", "speaker_id": mafia_id, "channel": "mafia_team", "message": "밤에는 조용히 찍자."},
            )

            mafia_view = mafia_game_payload(root, "mafia-room", viewer_agent_id=mafia_id)
            town_view = mafia_game_payload(root, "mafia-room", viewer_agent_id=town_id)
            host_view = mafia_game_payload(root, "mafia-room", viewer_agent_id="host")

            self.assertIn("밤에는 조용히 찍자.", _event_messages(mafia_view))
            self.assertNotIn("밤에는 조용히 찍자.", _event_messages(town_view))
            self.assertIn("밤에는 조용히 찍자.", _event_messages(host_view))
            self.assertIn("전체 채팅입니다.", _event_messages(town_view))
            self.assertEqual(_own_role(mafia_view, mafia_id), "mafia")
            self.assertEqual(_own_role(town_view, town_id), "town")
            self.assertNotIn("role", next(player for player in town_view["players"] if player["agent_id"] == mafia_id))

    def test_non_mafia_cannot_post_mafia_team_chat(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            game = start_mafia_game(root, {"game_id": "mafia-room", "players": PLAYERS, "mafia_count": 1})
            town_id = next(player["agent_id"] for player in game["players"] if player["role"] != "mafia")

            with self.assertRaises(ValueError):
                post_mafia_chat(
                    root,
                    {"game_id": "mafia-room", "speaker_id": town_id, "channel": "mafia_team", "message": "나도 팀챗 쓸래."},
                )

    def test_mafia_events_stay_out_of_lobby_side_chat_and_meeting_records(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            game = start_mafia_game(root, {"game_id": "mafia-room", "players": PLAYERS, "mafia_count": 1})
            mafia_id = _mafia_ids(game)[0]

            post_mafia_chat(
                root,
                {"game_id": "mafia-room", "speaker_id": mafia_id, "channel": "mafia_team", "message": "팀챗은 게임 기록에만 남습니다."},
            )

            self.assertTrue((root / "play" / "mafia" / "mafia-room.json").exists())
            self.assertFalse((root / "lobby.jsonl").exists())
            self.assertFalse((root / "side_chat.jsonl").exists())
            self.assertFalse((root / "meetings" / "mafia-room").exists())

    def test_day_and_night_votes_resolve_phase_and_winner(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            game = start_mafia_game(root, {"game_id": "mafia-room", "players": PLAYERS, "mafia_count": 1})
            mafia_id = _mafia_ids(game)[0]
            town_ids = [player["agent_id"] for player in game["players"] if player["role"] != "mafia"]

            for voter in town_ids[:2]:
                cast_mafia_vote(root, {"game_id": "mafia-room", "voter_id": voter, "target_id": mafia_id})
            resolved = resolve_mafia_phase(root, {"game_id": "mafia-room"})

            self.assertEqual(resolved["phase"], "ended")
            self.assertEqual(resolved["winner"], "town")
            self.assertFalse(next(player["alive"] for player in resolved["players"] if player["agent_id"] == mafia_id))

    def test_night_vote_resolves_without_exposing_mafia_ballot_to_town(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            game = start_mafia_game(root, {"game_id": "mafia-room", "players": PLAYERS, "mafia_count": 1})
            mafia_id = _mafia_ids(game)[0]
            town_id = next(player["agent_id"] for player in game["players"] if player["role"] != "mafia")

            night = resolve_mafia_phase(root, {"game_id": "mafia-room"})
            self.assertEqual(night["phase"], "night")

            cast_mafia_vote(root, {"game_id": "mafia-room", "voter_id": mafia_id, "target_id": town_id})
            town_view_before_resolution = mafia_game_payload(root, "mafia-room", viewer_agent_id=town_id)
            self.assertNotIn("votes", town_view_before_resolution)
            self.assertNotIn(f"{mafia_id} → {town_id}", _event_messages(town_view_before_resolution))

            resolved = resolve_mafia_phase(root, {"game_id": "mafia-room"})

            self.assertEqual(resolved["phase"], "day")
            self.assertEqual(resolved["day_number"], 2)
            self.assertFalse(next(player["alive"] for player in resolved["players"] if player["agent_id"] == town_id))
            self.assertIn("사망했습니다.", " ".join(_event_messages(resolved)))

    def test_town_filtered_payload_hides_night_votes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            game = start_mafia_game(root, {"game_id": "mafia-room", "players": PLAYERS, "mafia_count": 1})
            mafia_id = _mafia_ids(game)[0]
            town_id = next(player["agent_id"] for player in game["players"] if player["role"] != "mafia")

            resolve_mafia_phase(root, {"game_id": "mafia-room"})
            cast_mafia_vote(root, {"game_id": "mafia-room", "voter_id": mafia_id, "target_id": town_id})

            town_view = mafia_game_payload(root, "mafia-room", viewer_agent_id=town_id)
            host_view = mafia_game_payload(root, "mafia-room", viewer_agent_id="host")

            self.assertNotIn("votes", town_view)
            self.assertEqual(host_view["votes"]["night"], {mafia_id: town_id})

    def test_classic_rules_can_assign_doctor_and_detective(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            game = start_mafia_game(root, {"game_id": "mafia-room", "players": PLAYERS + [{"agent_id": "spark-e", "display_name": "Codex Spark E"}], "ruleset": "classic"})

            roles = {player["role"] for player in game["players"]}

            self.assertIn("mafia", roles)
            self.assertIn("doctor", roles)
            self.assertIn("detective", roles)

    def test_doctor_can_save_night_kill_target(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            game = start_mafia_game(root, {"game_id": "mafia-room", "players": CLASSIC_PLAYERS})
            resolve_mafia_phase(root, {"game_id": "mafia-room"})

            cast_mafia_vote(root, {"game_id": "mafia-room", "voter_id": "mafia-a", "target_id": "town-a"})
            submit_mafia_action(root, {"game_id": "mafia-room", "actor_id": "doctor-a", "action": "doctor_save", "target_id": "town-a"})
            resolved = resolve_mafia_phase(root, {"game_id": "mafia-room"})

            self.assertEqual(resolved["phase"], "day")
            self.assertTrue(next(player["alive"] for player in resolved["players"] if player["agent_id"] == "town-a"))
            self.assertIn("아무도 사망하지 않았습니다.", " ".join(_event_messages(resolved)))

    def test_detective_result_is_private_to_detective_and_host(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            start_mafia_game(root, {"game_id": "mafia-room", "players": CLASSIC_PLAYERS})
            resolve_mafia_phase(root, {"game_id": "mafia-room"})

            submit_mafia_action(root, {"game_id": "mafia-room", "actor_id": "detective-a", "action": "detective_check", "target_id": "mafia-a"})

            detective_view = mafia_game_payload(root, "mafia-room", viewer_agent_id="detective-a")
            town_view = mafia_game_payload(root, "mafia-room", viewer_agent_id="town-a")
            host_view = mafia_game_payload(root, "mafia-room", viewer_agent_id="host")

            self.assertIn("조사 결과", " ".join(_event_messages(detective_view)))
            self.assertNotIn("조사 결과", " ".join(_event_messages(town_view)))
            self.assertIn("조사 결과", " ".join(_event_messages(host_view)))

    def test_mafia_players_can_see_mafia_teammates(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            players = [
                {"agent_id": "mafia-a", "display_name": "Mafia A", "role": "mafia"},
                {"agent_id": "mafia-b", "display_name": "Mafia B", "role": "mafia"},
                {"agent_id": "town-a", "display_name": "Town A", "role": "town"},
                {"agent_id": "town-b", "display_name": "Town B", "role": "town"},
            ]
            start_mafia_game(root, {"game_id": "mafia-room", "players": players})

            mafia_view = mafia_game_payload(root, "mafia-room", viewer_agent_id="mafia-a")
            town_view = mafia_game_payload(root, "mafia-room", viewer_agent_id="town-a")

            self.assertEqual(_own_role(mafia_view, "mafia-b"), "mafia")
            self.assertIsNone(_own_role(town_view, "mafia-b"))

    def test_api_start_chat_vote_and_filtered_read(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base_url = f"http://127.0.0.1:{server.server_port}"
                started = _post(base_url, "/api/play/mafia/start", {"game_id": "mafia-room", "players": PLAYERS, "mafia_count": 1})
                mafia_id = _mafia_ids(started["game"])[0]
                town_id = next(player["agent_id"] for player in started["game"]["players"] if player["agent_id"] != mafia_id)

                _post(base_url, "/api/play/mafia/chat", {"game_id": "mafia-room", "speaker_id": mafia_id, "channel": "mafia_team", "message": "팀채팅"})
                _post(base_url, "/api/play/mafia/chat", {"game_id": "mafia-room", "speaker_id": town_id, "channel": "all", "message": "전체채팅"})
                _post(base_url, "/api/play/mafia/vote", {"game_id": "mafia-room", "voter_id": town_id, "target_id": mafia_id})

                with self.assertRaises(HTTPError) as error:
                    _post(base_url, "/api/play/mafia/chat", {"game_id": "mafia-room", "speaker_id": town_id, "channel": "mafia_team", "message": "불법 팀채팅"})
                self.assertEqual(error.exception.code, 400)
                error.exception.read()
                error.exception.close()

                town_view = _get(base_url, f"/api/play/mafia?game_id=mafia-room&viewer_agent_id={town_id}")
                mafia_view = _get(base_url, f"/api/play/mafia?game_id=mafia-room&viewer_agent_id={mafia_id}")
            finally:
                server.shutdown()
                server.server_close()

            self.assertNotIn("팀채팅", _event_messages(town_view["game"]))
            self.assertIn("팀채팅", _event_messages(mafia_view["game"]))
            self.assertIn("전체채팅", _event_messages(town_view["game"]))

    def test_api_resolve_can_return_filtered_view(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base_url = f"http://127.0.0.1:{server.server_port}"
                started = _post(base_url, "/api/play/mafia/start", {"game_id": "mafia-room", "players": PLAYERS, "mafia_count": 1})
                mafia_id = _mafia_ids(started["game"])[0]
                town_id = next(player["agent_id"] for player in started["game"]["players"] if player["agent_id"] != mafia_id)

                _post(base_url, "/api/play/mafia/chat", {"game_id": "mafia-room", "speaker_id": mafia_id, "channel": "mafia_team", "message": "비밀 팀챗"})
                filtered = _post(base_url, "/api/play/mafia/resolve", {"game_id": "mafia-room", "viewer_agent_id": town_id})
            finally:
                server.shutdown()
                server.server_close()

            self.assertNotIn("비밀 팀챗", _event_messages(filtered["game"]))
            self.assertNotIn("votes", filtered["game"])
            self.assertNotIn("role", next(player for player in filtered["game"]["players"] if player["agent_id"] == mafia_id))

    def test_api_doctor_action_can_save_target(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base_url = f"http://127.0.0.1:{server.server_port}"
                _post(base_url, "/api/play/mafia/start", {"game_id": "mafia-room", "players": CLASSIC_PLAYERS})
                _post(base_url, "/api/play/mafia/resolve", {"game_id": "mafia-room"})
                _post(base_url, "/api/play/mafia/vote", {"game_id": "mafia-room", "voter_id": "mafia-a", "target_id": "town-a"})
                _post(
                    base_url,
                    "/api/play/mafia/action",
                    {"game_id": "mafia-room", "actor_id": "doctor-a", "action": "doctor_save", "target_id": "town-a"},
                )
                resolved = _post(base_url, "/api/play/mafia/resolve", {"game_id": "mafia-room", "viewer_agent_id": "host"})
            finally:
                server.shutdown()
                server.server_close()

            self.assertTrue(next(player["alive"] for player in resolved["game"]["players"] if player["agent_id"] == "town-a"))


def _mafia_ids(game):
    return [player["agent_id"] for player in game["players"] if player.get("role") == "mafia"]


def _event_messages(game):
    return [event["message"] for event in game["events"]]


def _own_role(game, agent_id):
    return next(player.get("role") for player in game["players"] if player["agent_id"] == agent_id)


def _post(base_url, path, payload):
    request = Request(
        base_url + path,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=4) as response:
        return json.loads(response.read().decode("utf-8"))


def _get(base_url, path):
    with urlopen(base_url + path, timeout=4) as response:
        return json.loads(response.read().decode("utf-8"))
