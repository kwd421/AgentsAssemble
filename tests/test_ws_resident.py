"""End-to-end: an agent running its loop over WS (WS-resident path, task #39).

A resident connects over the governed WS, receives a human message pushed to it,
runs its brain, and says the reply over WS — all against a real server. Proves
the exact path that replaces the HTTP poll loop. The brain is a stub here (no
model/key needed); production swaps in codex/an API call.
"""
import json
import shutil
import threading
import tempfile
import time
import unittest
from unittest import mock
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request, urlopen

import agentsassemble.ws_resident as ws_resident
from agentsassemble.gui import _make_handler
from agentsassemble.live_agent_runner import ResidentAgentConfig
from agentsassemble.room_invite import create_room_invite, join_room_with_invite, reset_state
from agentsassemble.ws_resident import run_provider_ws_resident, run_ws_resident


def _resident_config(agent_id: str, *, engagement_mode: str = "always") -> ResidentAgentConfig:
    return ResidentAgentConfig(
        server="", agent_id=agent_id, display_name=agent_id,
        provider_kind="codex_live_session", connection_kind="live_session", session_id="",
        endpoint="", auth_ref="", meeting_id="room-1", engagement_mode=engagement_mode,
        command=["codex"], timeout_seconds=60, poll_interval=1.0, heartbeat_interval=30.0,
        cooldown=1.0, max_chain_depth=1,
    )


class WsResidentLiveTests(unittest.TestCase):
    def setUp(self):
        reset_state()
        self._servers: list[ThreadingHTTPServer] = []

    def tearDown(self):
        for server in self._servers:
            server.shutdown()
            server.server_close()
        reset_state()

    def _start(self, root: Path) -> str:
        server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(root))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self._servers.append(server)
        host, port = server.server_address
        return f"http://{host}:{port}"

    def _token(self, base: str, agent_id: str, participant_type: str) -> str:
        invite = create_room_invite(
            room_url=base,
            meeting_id="room-1",
            agent_id=agent_id,
            display_name=agent_id,
            participant_type=participant_type,
            max_uses=1,
        )
        return str(join_room_with_invite(str(invite["invite_token"]))["session_token"])

    def _post_say(self, base: str, token: str, message: str) -> None:
        request = Request(
            f"{base}/api/room/say",
            data=json.dumps({"message": message}).encode("utf-8"),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=4):
            pass

    def _lobby_messages(self, base: str, token: str) -> list[dict]:
        request = Request(f"{base}/api/room/lobby", headers={"Authorization": f"Bearer {token}"})
        with urlopen(request, timeout=4) as response:
            return json.loads(response.read().decode("utf-8")).get("events", [])

    def test_resident_replies_to_a_human_message_over_ws(self):
        # mkdtemp + addCleanup so the server is shut down (tearDown) BEFORE the
        # dir is removed (addCleanup, LIFO after tearDown); ignore residual WAL files.
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        base = self._start(Path(tmp))
        agent_token = self._token(base, "echo-bot", "agent")
        human_token = self._token(base, "human-1", "human")

        result: dict = {}

        def resident():
            # stub brain: echo. max_replies=1 → loop returns after one reply;
            # max_idle_rounds bounds the wait so the test can't hang.
            result["replies"] = run_ws_resident(
                base,
                agent_token,
                brain=lambda ev: f"echo: {ev.get('message','')}",
                max_replies=1,
                max_idle_rounds=60,  # ~30s safety at 0.5s/round
            )

        thread = threading.Thread(target=resident, daemon=True)
        thread.start()

        # human speaks; the resident receives it pushed over WS and replies
        self._post_say(base, human_token, "안녕 상주야")
        thread.join(timeout=20)
        self.assertFalse(thread.is_alive(), "resident loop did not finish")
        self.assertEqual(result.get("replies"), 1)

        # The WS say is async (the server appends in its own handler thread), so
        # poll the lobby until the reply lands (eventual consistency).
        replies: list[dict] = []
        for _ in range(50):  # up to ~5s
            messages = self._lobby_messages(base, human_token)
            replies = [m for m in messages if str(m.get("message", "")).startswith("echo:")]
            if replies:
                break
            time.sleep(0.1)
        self.assertTrue(replies, "resident reply never appeared in the lobby over WS")
        self.assertEqual(replies[-1]["message"], "echo: 안녕 상주야")
        self.assertEqual(replies[-1]["actor_id"], "echo-bot")


class ProviderWsResidentTests(WsResidentLiveTests):
    """A provider agent over WS at the runner's prompt fidelity (delegate_prompt
    + engagement), with a stub command_runner standing in for codex/grok."""

    def test_provider_resident_replies_with_envelope_prompt(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        base = self._start(Path(tmp))
        agent_token = self._token(base, "codex-ws", "agent")
        human_token = self._token(base, "human-1", "human")

        captured: list[str] = []

        def stub_command_runner(command, prompt, *, timeout_seconds):
            captured.append(prompt)
            return "내 생각엔 좀 다른데, 근거를 보면..."

        result: dict = {}

        def resident():
            result["replies"] = run_provider_ws_resident(
                base,
                agent_token,
                _resident_config("codex-ws"),
                stub_command_runner,
                max_replies=1,
                max_idle_rounds=80,
            )

        thread = threading.Thread(target=resident, daemon=True)
        thread.start()
        time.sleep(0.6)  # let it connect + seed (skip history)
        self._post_say(base, human_token, "이거 맞지? 동의하지?")
        thread.join(timeout=20)
        self.assertFalse(thread.is_alive(), "provider resident did not finish")
        self.assertEqual(result.get("replies"), 1)

        # the reply landed
        replies = []
        for _ in range(50):
            messages = self._lobby_messages(base, human_token)
            replies = [m for m in messages if m.get("actor_id") == "codex-ws"]
            if replies:
                break
            time.sleep(0.1)
        self.assertTrue(replies, "provider resident reply never appeared")
        self.assertEqual(replies[-1]["message"], "내 생각엔 좀 다른데, 근거를 보면...")
        human_events = [m for m in messages if m.get("actor_id") == "human-1"]
        self.assertTrue(human_events, "source human event missing")
        self.assertEqual(replies[-1]["source_event_id"], human_events[-1]["id"])
        self.assertEqual(replies[-1]["auto_chain_depth"], 1)
        self.assertEqual(replies[-1]["flow_meeting_id"], "room-1")

        # and the brain got the full runner envelope, not a raw message
        self.assertTrue(captured, "command_runner was never called")
        prompt = captured[-1]
        self.assertIn("Agent id: codex-ws", prompt)
        self.assertIn("이거 맞지? 동의하지?", prompt)

    def test_provider_resident_does_not_drop_live_event_batched_with_snapshot(self):
        class FakeSock:
            def settimeout(self, _timeout):
                pass

        class FakeClient:
            def __init__(self):
                self.sock = FakeSock()
                self.closed = False
                self.sent: list[dict] = []
                self._messages = [
                    [
                        {"op": "subscribed", "streams": ["lobby"]},
                        {
                            "op": "event",
                            "stream": "lobby",
                            "snapshot": True,
                            "events": [],
                        },
                        {
                            "op": "event",
                            "stream": "lobby",
                            "events": [{"id": "new", "actor_type": "human", "name": "Human", "message": "new"}],
                        },
                    ]
                ]

            def receive(self):
                return self._messages.pop(0) if self._messages else []

            def thinking(self, on: bool):
                self.sent.append({"op": "thinking", "on": on})

            def say(self, message: str, **extra):
                self.sent.append({"op": "say", "message": message, **extra})
                return {"op": "ack", "event": {"id": "reply1"}}

            def close(self):
                self.closed = True

        fake = FakeClient()

        def stub_command_runner(command, prompt, *, timeout_seconds):
            self.assertIn("new", prompt)
            return "reply to new"

        with patch.object(ws_resident, "connect_room_ws", return_value=fake):
            replies = run_provider_ws_resident(
                "http://room.local",
                "session-token",
                _resident_config("codex-ws"),
                stub_command_runner,
                max_replies=1,
                max_idle_rounds=1,
            )

        self.assertEqual(replies, 1)
        say_messages = [message for message in fake.sent if message.get("op") == "say"]
        self.assertEqual(len(say_messages), 1)
        self.assertEqual(say_messages[0]["message"], "reply to new")
        self.assertEqual(say_messages[0]["source_event_id"], "new")

    def test_provider_resident_keeps_snapshot_cursor_when_ack_is_split(self):
        class FakeSock:
            def settimeout(self, _timeout):
                pass

        class FakeClient:
            def __init__(self):
                self.sock = FakeSock()
                self.closed = False
                self.sent: list[dict] = []
                self._messages = [
                    [{"op": "subscribed", "streams": ["lobby"]}],
                    [
                        {
                            "op": "event",
                            "stream": "lobby",
                            "snapshot": True,
                            "events": [{"id": "old", "actor_type": "human", "name": "Human", "message": "old"}],
                        }
                    ],
                    [
                        {
                            "op": "event",
                            "stream": "lobby",
                            "events": [{"id": "new", "actor_type": "human", "name": "Human", "message": "new"}],
                        }
                    ],
                ]

            def receive(self):
                return self._messages.pop(0) if self._messages else []

            def thinking(self, on: bool):
                self.sent.append({"op": "thinking", "on": on})

            def say(self, message: str, **extra):
                self.sent.append({"op": "say", "message": message, **extra})
                return {"op": "ack", "event": {"id": "reply1"}}

            def close(self):
                self.closed = True

        fake = FakeClient()

        with patch.object(ws_resident, "connect_room_ws", return_value=fake):
            replies = run_provider_ws_resident(
                "http://room.local",
                "session-token",
                _resident_config("codex-ws"),
                lambda command, prompt, *, timeout_seconds: "reply",
                max_replies=1,
                max_idle_rounds=1,
            )

        self.assertEqual(replies, 1)
        say_messages = [message for message in fake.sent if message.get("op") == "say"]
        self.assertEqual(len(say_messages), 1)
        self.assertEqual(say_messages[0]["source_event_id"], "new")

    def test_provider_resident_answers_first_live_event_after_empty_snapshot_boundary(self):
        class FakeSock:
            def settimeout(self, _timeout):
                pass

        class FakeClient:
            def __init__(self):
                self.sock = FakeSock()
                self.closed = False
                self.sent: list[dict] = []
                self._messages = [
                    [
                        {"op": "subscribed", "streams": ["lobby"]},
                        {
                            "op": "event",
                            "stream": "lobby",
                            "snapshot": True,
                            "events": [],
                        },
                        {
                            "op": "event",
                            "stream": "lobby",
                            "events": [{"id": "new", "actor_type": "human", "name": "Human", "message": "new"}],
                        },
                    ]
                ]

            def receive(self):
                return self._messages.pop(0) if self._messages else []

            def thinking(self, on: bool):
                self.sent.append({"op": "thinking", "on": on})

            def say(self, message: str, **extra):
                self.sent.append({"op": "say", "message": message, **extra})
                return {"op": "ack", "event": {"id": "reply1"}}

            def close(self):
                self.closed = True

        fake = FakeClient()

        with patch.object(ws_resident, "connect_room_ws", return_value=fake):
            replies = run_provider_ws_resident(
                "http://room.local",
                "session-token",
                _resident_config("codex-ws"),
                lambda command, prompt, *, timeout_seconds: "reply",
                max_replies=1,
                max_idle_rounds=1,
            )

        self.assertEqual(replies, 1)
        say_messages = [message for message in fake.sent if message.get("op") == "say"]
        self.assertEqual(len(say_messages), 1)
        self.assertEqual(say_messages[0]["source_event_id"], "new")

    def test_engagement_override_makes_resident_reply_to_unmentioned_peer(self):
        # A peer agent's message that does NOT mention us: with the agent's own
        # "mentioned" default the resident stays silent; the free-flow override
        # ("always") makes it reply. Proves the engagement_mode param reaches the
        # reply decision, not just storage.
        def make_client():
            class FakeSock:
                def settimeout(self, _timeout):
                    pass

            class FakeClient:
                def __init__(self):
                    self.sock = FakeSock()
                    self.closed = False
                    self.sent: list[dict] = []
                    self._messages = [
                        [
                            {"op": "subscribed", "streams": ["lobby"]},
                            {"op": "event", "stream": "lobby", "snapshot": True, "events": []},
                            {
                                "op": "event",
                                "stream": "lobby",
                                "events": [{
                                    "id": "peer1", "actor_type": "agent", "actor_id": "other-bot",
                                    "name": "OtherBot", "message": "작업을 나눠보자",
                                }],
                            },
                        ]
                    ]

                def receive(self):
                    return self._messages.pop(0) if self._messages else []

                def thinking(self, on: bool):
                    pass

                def say(self, message: str, **extra):
                    self.sent.append({"op": "say", "message": message, **extra})
                    return {"op": "ack", "event": {"id": "reply1"}}

                def close(self):
                    self.closed = True

            return FakeClient()

        config = _resident_config("codex-ws", engagement_mode="mentioned")

        silent = make_client()
        with patch.object(ws_resident, "connect_room_ws", return_value=silent):
            silent_replies = run_provider_ws_resident(
                "http://room.local", "session-token", config,
                lambda command, prompt, *, timeout_seconds: "reply",
                max_replies=1, max_idle_rounds=1,
            )
        self.assertEqual(silent_replies, 0)
        self.assertEqual([m for m in silent.sent if m.get("op") == "say"], [])

        chatty = make_client()
        with patch.object(ws_resident, "connect_room_ws", return_value=chatty):
            chatty_replies = run_provider_ws_resident(
                "http://room.local", "session-token", config,
                lambda command, prompt, *, timeout_seconds: "reply",
                max_replies=1, max_idle_rounds=1,
                engagement_mode="always",
            )
        self.assertEqual(chatty_replies, 1)
        say_messages = [m for m in chatty.sent if m.get("op") == "say"]
        self.assertEqual(len(say_messages), 1)
        self.assertEqual(say_messages[0]["source_event_id"], "peer1")

    def test_resident_passes_configured_command_to_runner(self):
        # The WS loop must hand the runner config.command, not []. Provider runners
        # (codex/grok) ignore it, but generic terminal/jsonl runners (claude_code
        # via terminal_session) need it or they raise "Live session command is
        # required". Regression for the claude_code launch fix.
        class FakeSock:
            def settimeout(self, _timeout):
                pass

        class FakeClient:
            def __init__(self):
                self.sock = FakeSock()
                self.closed = False
                self.sent: list[dict] = []
                self._messages = [
                    [
                        {"op": "subscribed", "streams": ["lobby"]},
                        {"op": "event", "stream": "lobby", "snapshot": True, "events": []},
                        {
                            "op": "event",
                            "stream": "lobby",
                            "events": [{"id": "new", "actor_type": "human", "name": "Human", "message": "hi"}],
                        },
                    ]
                ]

            def receive(self):
                return self._messages.pop(0) if self._messages else []

            def thinking(self, on: bool):
                pass

            def say(self, message: str, **extra):
                self.sent.append({"op": "say", "message": message, **extra})
                return {"op": "ack", "event": {"id": "reply1"}}

            def close(self):
                self.closed = True

        captured: list[list[str]] = []

        def stub_command_runner(command, prompt, *, timeout_seconds):
            captured.append(list(command))
            return "reply"

        import dataclasses
        config = dataclasses.replace(_resident_config("claude-bot"), command=["claude"])

        fake = FakeClient()
        with patch.object(ws_resident, "connect_room_ws", return_value=fake):
            run_provider_ws_resident(
                "http://room.local", "session-token", config, stub_command_runner,
                max_replies=1, max_idle_rounds=1,
            )

        self.assertTrue(captured, "command_runner was never called")
        self.assertEqual(captured[-1], ["claude"])  # config.command, not []

    def test_provider_resident_does_not_count_rejected_say_as_reply(self):
        class FakeSock:
            def settimeout(self, _timeout):
                pass

        class FakeClient:
            def __init__(self):
                self.sock = FakeSock()
                self.closed = False
                self.sent: list[dict] = []
                self._messages = [
                    [
                        {"op": "subscribed", "streams": ["lobby"]},
                        {
                            "op": "event",
                            "stream": "lobby",
                            "snapshot": True,
                            "events": [{"id": "old", "actor_type": "human", "name": "Human", "message": "old"}],
                        },
                        {
                            "op": "event",
                            "stream": "lobby",
                            "events": [{"id": "new", "actor_type": "human", "name": "Human", "message": "new"}],
                        },
                    ],
                    [{"op": "error", "category": "muted", "message": "muted"}],
                ]

            def receive(self):
                return self._messages.pop(0) if self._messages else []

            def thinking(self, on: bool):
                self.sent.append({"op": "thinking", "on": on})

            def say(self, message: str, **extra):
                self.sent.append({"op": "say", "message": message, **extra})

            def close(self):
                self.closed = True

        fake = FakeClient()

        with patch.object(ws_resident, "connect_room_ws", return_value=fake):
            replies = run_provider_ws_resident(
                "http://room.local",
                "session-token",
                _resident_config("codex-ws"),
                lambda command, prompt, *, timeout_seconds: "reply",
                max_replies=1,
                max_idle_rounds=1,
            )

        self.assertEqual(replies, 0)
        say_messages = [message for message in fake.sent if message.get("op") == "say"]
        self.assertEqual(len(say_messages), 1)


class WsLaunchWiringTests(unittest.TestCase):
    """The one-command WS launch (`live-agent run --transport ws`) wires the
    provider brain + session into run_provider_ws_resident. Stubbed — no network."""

    def _args(self, *extra):
        from agentsassemble.cli import build_parser

        return build_parser().parse_args([
            "live-agent", "run", "--server", "http://127.0.0.1:8765",
            "--agent-id", "codex-ws", "--display-name", "Codex",
            "--provider-kind", "codex_live_session", "--connection-kind", "live_session",
            "--transport", "ws", "--engagement-mode", "always",
            *extra, "--command", "codex",
        ])

    def test_session_token_is_passed_through(self):
        import agentsassemble.cli as cli
        from agentsassemble.live_agent_runner import config_from_args

        seen = {}

        def fake_run(server, token, config, runner, *, max_replies=0, engagement_mode=None, use_floor=False):
            seen.update(
                server=server, token=token, agent_id=config.agent_id,
                max_replies=max_replies, engagement_mode=engagement_mode, use_floor=use_floor,
            )
            return 2

        args = self._args("--session-token", "tok-123", "--max-ticks", "2")
        config = config_from_args(args)
        with mock.patch("agentsassemble.ws_room_client.fetch_room_conversation_mode", lambda *a, **k: "quiet"), \
             mock.patch("agentsassemble.ws_resident.run_provider_ws_resident", fake_run):
            rc = cli._run_ws_resident_command(args, config)
        self.assertEqual(rc, 0)
        self.assertEqual(seen["token"], "tok-123")
        self.assertEqual(seen["agent_id"], "codex-ws")
        self.assertEqual(seen["max_replies"], 2)
        # a "quiet" room → agents speak only when mentioned; no turn floor.
        self.assertEqual(seen["engagement_mode"], "mentioned")
        self.assertFalse(seen["use_floor"])

    def test_free_room_resolves_engagement_to_always(self):
        import agentsassemble.cli as cli
        from agentsassemble.cli import build_parser
        from agentsassemble.live_agent_runner import config_from_args

        seen = {}
        # Agent default is "mentioned"; a free room overrides it to "always" (no floor).
        args = build_parser().parse_args([
            "live-agent", "run", "--server", "http://127.0.0.1:8765",
            "--agent-id", "codex-ws", "--display-name", "Codex",
            "--provider-kind", "codex_live_session", "--connection-kind", "live_session",
            "--transport", "ws", "--engagement-mode", "mentioned",
            "--session-token", "tok-123", "--command", "codex",
        ])
        config = config_from_args(args)
        with mock.patch("agentsassemble.ws_room_client.fetch_room_conversation_mode", lambda *a, **k: "free"), \
             mock.patch("agentsassemble.ws_resident.run_provider_ws_resident",
                        lambda server, token, cfg, runner, *, max_replies=0, engagement_mode=None, use_floor=False:
                        seen.update(engagement_mode=engagement_mode, use_floor=use_floor) or 0):
            cli._run_ws_resident_command(args, config)
        self.assertEqual(seen["engagement_mode"], "always")
        self.assertFalse(seen["use_floor"])

    def test_ordered_room_resolves_to_always_with_floor(self):
        import agentsassemble.cli as cli
        from agentsassemble.cli import build_parser
        from agentsassemble.live_agent_runner import config_from_args

        seen = {}
        args = build_parser().parse_args([
            "live-agent", "run", "--server", "http://127.0.0.1:8765",
            "--agent-id", "codex-ws", "--provider-kind", "codex_live_session",
            "--connection-kind", "live_session", "--transport", "ws",
            "--engagement-mode", "mentioned", "--session-token", "tok-123", "--command", "codex",
        ])
        config = config_from_args(args)
        with mock.patch("agentsassemble.ws_room_client.fetch_room_conversation_mode", lambda *a, **k: "ordered"), \
             mock.patch("agentsassemble.ws_resident.run_provider_ws_resident",
                        lambda server, token, cfg, runner, *, max_replies=0, engagement_mode=None, use_floor=False:
                        seen.update(engagement_mode=engagement_mode, use_floor=use_floor) or 0):
            cli._run_ws_resident_command(args, config)
        self.assertEqual(seen["engagement_mode"], "always")  # ordered: everyone wants to talk
        self.assertTrue(seen["use_floor"])  # ...but the floor algorithm spaces them out

    def test_invite_token_is_joined_for_a_session(self):
        import agentsassemble.cli as cli
        from agentsassemble.live_agent_runner import config_from_args

        seen = {}
        args = self._args("--invite-token", "inv-xyz")
        config = config_from_args(args)
        with mock.patch("agentsassemble.ws_room_client.fetch_room_conversation_mode", lambda *a, **k: "quiet"), \
             mock.patch("agentsassemble.ws_room_client.join_room_session", lambda *a, **k: "tok-from-invite") as _j, \
             mock.patch("agentsassemble.ws_resident.run_provider_ws_resident",
                        lambda server, token, cfg, runner, *, max_replies=0, engagement_mode=None, use_floor=False:
                        seen.update(token=token) or 0):
            cli._run_ws_resident_command(args, config)
        self.assertEqual(seen["token"], "tok-from-invite")

    def test_requires_token_or_invite(self):
        import agentsassemble.cli as cli
        from agentsassemble.live_agent_runner import config_from_args

        args = self._args()  # no session-token, no invite-token
        config = config_from_args(args)
        with self.assertRaises(ValueError):
            cli._run_ws_resident_command(args, config)


if __name__ == "__main__":
    unittest.main()
