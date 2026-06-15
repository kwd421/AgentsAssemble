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
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen

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

        # and the brain got the full runner envelope, not a raw message
        self.assertTrue(captured, "command_runner was never called")
        prompt = captured[-1]
        self.assertIn("Agent id: codex-ws", prompt)
        self.assertIn("이거 맞지? 동의하지?", prompt)


if __name__ == "__main__":
    unittest.main()
