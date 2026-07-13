import json
import struct
import unittest

from agentsassemble.room_websocket import OP_CLOSE, OP_PING, OP_PONG, OP_TEXT
from agentsassemble.ws_room_session import (
    WS_SESSION_REVOKED_CATEGORY,
    WsRoomDeps,
    WsRoomSession,
    WsSayRejected,
    WsCommandRejected,
    WsTicketStore,
)


def server_frames(frames):
    """Decode a list of server→client (unmasked) frames into (opcode, payload)."""
    out = []
    for frame in frames:
        b0, b1 = frame[0], frame[1]
        opcode = b0 & 0x0F
        length = b1 & 0x7F
        offset = 2
        if length == 126:
            (length,) = struct.unpack("!H", frame[2:4])
            offset = 4
        elif length == 127:
            (length,) = struct.unpack("!Q", frame[2:10])
            offset = 10
        out.append((opcode, frame[offset:offset + length]))
    return out


def text_messages(frames):
    """JSON objects from server TEXT frames."""
    return [json.loads(payload.decode("utf-8")) for op, payload in server_frames(frames) if op == OP_TEXT]


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class TicketStoreTests(unittest.TestCase):
    def test_issue_then_consume_returns_session(self):
        store = WsTicketStore()
        ticket = store.issue({"agent_id": "guest-1", "meeting_id": "room-1"})
        session = store.consume(ticket)
        self.assertEqual(session["agent_id"], "guest-1")

    def test_ticket_is_single_use(self):
        store = WsTicketStore()
        ticket = store.issue({"agent_id": "guest-1"})
        self.assertIsNotNone(store.consume(ticket))
        self.assertIsNone(store.consume(ticket))  # second use fails

    def test_expired_ticket_rejected(self):
        clock = FakeClock()
        store = WsTicketStore(ttl_seconds=30, now_fn=clock)
        ticket = store.issue({"agent_id": "guest-1"})
        clock.t += 31
        self.assertIsNone(store.consume(ticket))

    def test_unknown_ticket_is_none(self):
        self.assertIsNone(WsTicketStore().consume("wst_nope"))


class FakeDeps:
    def __init__(self):
        self.lobby_queue = []          # events to hand out once
        self.lobby_latest = ""
        self.roster = ([], "sig0")
        self.muted = False
        self.session_active = True
        self.posted = []
        self.statuses = []             # (identity, status) from set_status
        self.room_snapshot = {
            "room": {"room_id": "room-1"},
            "participants": [],
            "agent_sessions": [],
            "active_turns": [],
            "events": [{"id": "canonical-1", "seq": 1, "type": "message_final"}],
            "last_seq": 1,
            "capabilities": {"message.send": True},
        }
        self.commands = []
        self.subscriptions = []

    def make(self):
        return WsRoomDeps(
            read_lobby_after=self._read_lobby_after,
            read_roster=lambda meeting_id: self.roster,
            read_side_chat_after=lambda meeting_id, after_id: ([], after_id),
            post_say=self._post_say,
            is_muted=lambda meeting_id, agent_id: self.muted,
            set_thinking=lambda identity, on: self.statuses.append((identity, on)),
            is_session_active=lambda session_token: self.session_active,
            room_snapshot=lambda identity, after_seq: {**self.room_snapshot, "after_seq": after_seq},
            execute_command=self._execute_command,
            on_subscribe=lambda identity, streams, after_seq: self.subscriptions.append((identity, streams, after_seq)),
        )

    def _read_lobby_after(self, meeting_id, after):
        events = self.lobby_queue
        self.lobby_queue = []
        return events, self.lobby_latest

    def _post_say(self, identity, payload):
        event = {"id": "evt1", "message": payload["message"], "actor_id": identity["agent_id"]}
        self.posted.append((identity, payload))
        return event

    def _execute_command(self, identity, message):
        self.commands.append((identity, message))
        if message.get("action") == "reject":
            raise WsCommandRejected("no", code="permission_denied")
        return {
            "op": "ack",
            "request_id": message.get("request_id"),
            "accepted": True,
            "action": message.get("action"),
        }


def _session(deps, *, session_token="", **identity_over):
    identity = {
        "agent_id": "guest-1",
        "display_name": "테스터",
        "participant_type": "human",
        "client_type": "browser",
        "invite_scope": "read_write",
        "meeting_id": "room-1",
        "operator": False,
    }
    identity.update(identity_over)
    return WsRoomSession(identity=identity, deps=deps.make(), session_token=session_token)


class SubscribeTests(unittest.TestCase):
    def test_room_events_subscription_returns_canonical_snapshot_and_resume_sequence(self):
        deps = FakeDeps()
        sess = _session(deps)

        msgs = text_messages(
            sess.handle_frame(
                OP_TEXT,
                json.dumps({"op": "subscribe", "streams": ["room_events"], "resume_from_seq": 7}).encode(),
            )
        )

        self.assertEqual(msgs[0], {"op": "subscribed", "streams": ["room_events"]})
        self.assertEqual(msgs[1]["op"], "snapshot")
        self.assertEqual(msgs[1]["stream"], "room_events")
        self.assertEqual(msgs[1]["events"][0]["seq"], 1)
        self.assertEqual(msgs[1]["after_seq"], 7)
        self.assertEqual(deps.subscriptions[0][1], {"room_events"})

    def test_subscribe_acks_and_pushes_snapshot(self):
        deps = FakeDeps()
        deps.lobby_queue = [{"id": "e1", "message": "hi"}]
        deps.lobby_latest = "e1"
        sess = _session(deps)
        frames = sess.handle_frame(OP_TEXT, json.dumps({"op": "subscribe", "streams": ["lobby"]}).encode())
        msgs = text_messages(frames)
        self.assertEqual(msgs[0]["op"], "subscribed")
        self.assertEqual(msgs[0]["streams"], ["lobby"])
        self.assertEqual(msgs[1]["op"], "event")
        self.assertTrue(msgs[1].get("snapshot"))
        self.assertEqual(msgs[1]["events"][0]["message"], "hi")

    def test_subscribe_pushes_empty_snapshot_boundary(self):
        deps = FakeDeps()
        sess = _session(deps)
        frames = sess.handle_frame(OP_TEXT, json.dumps({"op": "subscribe", "streams": ["lobby"]}).encode())
        msgs = text_messages(frames)
        self.assertEqual(msgs[0]["op"], "subscribed")
        self.assertGreaterEqual(len(msgs), 2)
        self.assertEqual(msgs[1]["op"], "event")
        self.assertEqual(msgs[1]["stream"], "lobby")
        self.assertTrue(msgs[1].get("snapshot"))
        self.assertEqual(msgs[1]["events"], [])

    def test_subscribe_defaults_to_all_streams(self):
        sess = _session(FakeDeps())
        frames = sess.handle_frame(OP_TEXT, json.dumps({"op": "subscribe"}).encode())
        self.assertEqual(set(text_messages(frames)[0]["streams"]), {"lobby", "roster", "side_chat"})

    def test_roster_only_pushed_on_signature_change(self):
        deps = FakeDeps()
        deps.roster = ([{"agent_id": "a"}], "sig1")
        sess = _session(deps)
        sess.handle_frame(OP_TEXT, json.dumps({"op": "subscribe", "streams": ["roster"]}).encode())
        # second poll with same signature → no roster frame
        self.assertEqual(sess.poll(), [])
        # signature change → push
        deps.roster = ([{"agent_id": "a"}, {"agent_id": "b"}], "sig2")
        msgs = text_messages(sess.poll())
        self.assertEqual(msgs[0]["stream"], "roster")
        self.assertEqual(len(msgs[0]["members"]), 2)


class SayGovernanceTests(unittest.TestCase):
    def test_say_posts_and_acks(self):
        deps = FakeDeps()
        sess = _session(deps)
        frames = sess.handle_frame(OP_TEXT, json.dumps({"op": "say", "message": "hello"}).encode())
        msgs = text_messages(frames)
        self.assertEqual(msgs[0]["op"], "ack")
        self.assertEqual(msgs[0]["event"]["message"], "hello")
        self.assertEqual(len(deps.posted), 1)
        # server injects identity, not the client
        identity, payload = deps.posted[0]
        self.assertEqual(identity["agent_id"], "guest-1")

    def test_say_forwards_safe_reply_metadata(self):
        deps = FakeDeps()
        sess = _session(deps)
        frames = sess.handle_frame(OP_TEXT, json.dumps({
            "op": "say",
            "message": "reply",
            "source_event_id": "src1",
            "auto_chain_depth": 2,
            "flow_meeting_id": "room-1",
            "actor_id": "spoofed-agent",
        }).encode())
        msgs = text_messages(frames)
        self.assertEqual(msgs[0]["op"], "ack")
        _identity, payload = deps.posted[0]
        self.assertEqual(payload.get("source_event_id"), "src1")
        self.assertEqual(payload.get("auto_chain_depth"), 2)
        self.assertEqual(payload.get("flow_meeting_id"), "room-1")
        self.assertNotIn("actor_id", payload)

    def test_read_only_scope_blocks_say(self):
        deps = FakeDeps()
        sess = _session(deps, invite_scope="read_only")
        msgs = text_messages(sess.handle_frame(OP_TEXT, json.dumps({"op": "say", "message": "x"}).encode()))
        self.assertEqual(msgs[0]["op"], "error")
        self.assertEqual(msgs[0]["category"], "read_only")
        self.assertEqual(deps.posted, [])

    def test_muted_blocks_say(self):
        deps = FakeDeps()
        deps.muted = True
        sess = _session(deps)
        msgs = text_messages(sess.handle_frame(OP_TEXT, json.dumps({"op": "say", "message": "x"}).encode()))
        self.assertEqual(msgs[0]["category"], "muted")
        self.assertEqual(deps.posted, [])

    def test_empty_message_rejected(self):
        deps = FakeDeps()
        sess = _session(deps)
        msgs = text_messages(sess.handle_frame(OP_TEXT, json.dumps({"op": "say", "message": "   "}).encode()))
        self.assertEqual(msgs[0]["category"], "empty")

    def test_post_say_rejection_becomes_error_frame(self):
        deps = FakeDeps()
        deps._post_say = lambda identity, payload: (_ for _ in ()).throw(
            WsSayRejected("someone spoke", category="turn_conflict")
        )
        sess = _session(deps)
        msgs = text_messages(sess.handle_frame(OP_TEXT, json.dumps({"op": "say", "message": "x"}).encode()))
        self.assertEqual(msgs[0]["category"], "turn_conflict")

    def test_revoked_backing_session_blocks_say(self):
        deps = FakeDeps()
        deps.session_active = False
        sess = _session(deps, session_token="aas1.dead")
        msgs = text_messages(sess.handle_frame(OP_TEXT, json.dumps({"op": "say", "message": "x"}).encode()))
        self.assertEqual(msgs[0]["op"], "error")
        self.assertEqual(msgs[0]["category"], WS_SESSION_REVOKED_CATEGORY)
        self.assertEqual(deps.posted, [])
        self.assertTrue(sess.closed)

    def test_revoked_backing_session_stops_polling(self):
        deps = FakeDeps()
        deps.session_active = False
        sess = _session(deps, session_token="aas1.dead")
        msgs = text_messages(sess.poll())
        self.assertEqual(msgs[0]["category"], WS_SESSION_REVOKED_CATEGORY)
        self.assertTrue(sess.closed)


class ThinkingTests(unittest.TestCase):
    def test_thinking_on_signals_true(self):
        deps = FakeDeps()
        sess = _session(deps)
        msgs = text_messages(sess.handle_frame(OP_TEXT, json.dumps({"op": "thinking", "on": True}).encode()))
        self.assertEqual(msgs[0], {"op": "thinking_ack", "on": True})
        self.assertEqual(deps.statuses[-1][1], True)
        self.assertEqual(deps.statuses[-1][0]["agent_id"], "guest-1")

    def test_thinking_off_signals_false(self):
        deps = FakeDeps()
        sess = _session(deps)
        sess.handle_frame(OP_TEXT, json.dumps({"op": "thinking", "on": False}).encode())
        self.assertEqual(deps.statuses[-1][1], False)


class ControlAndMiscTests(unittest.TestCase):
    def test_correlated_command_returns_ack(self):
        deps = FakeDeps()
        sess = _session(deps)
        msgs = text_messages(
            sess.handle_frame(
                OP_TEXT,
                json.dumps(
                    {
                        "op": "command",
                        "request_id": "req-1",
                        "action": "message.send",
                        "payload": {"content": "hello"},
                    }
                ).encode(),
            )
        )

        self.assertEqual(msgs[0]["op"], "ack")
        self.assertEqual(msgs[0]["request_id"], "req-1")
        self.assertEqual(deps.commands[0][0]["agent_id"], "guest-1")

    def test_rejected_command_returns_correlated_nack(self):
        deps = FakeDeps()
        sess = _session(deps)
        msgs = text_messages(
            sess.handle_frame(
                OP_TEXT,
                json.dumps({"op": "command", "request_id": "req-2", "action": "reject", "payload": {}}).encode(),
            )
        )

        self.assertEqual(msgs[0]["op"], "nack")
        self.assertEqual(msgs[0]["request_id"], "req-2")
        self.assertEqual(msgs[0]["error"]["code"], "permission_denied")

    def test_ping_returns_pong(self):
        sess = _session(FakeDeps())
        frames = sess.handle_frame(OP_PING, b"abc")
        self.assertEqual(server_frames(frames), [(OP_PONG, b"abc")])

    def test_close_marks_closed_and_polls_quiet(self):
        deps = FakeDeps()
        deps.lobby_queue = [{"id": "e1"}]
        sess = _session(deps)
        sess.handle_frame(OP_TEXT, json.dumps({"op": "subscribe", "streams": ["lobby"]}).encode())
        out = sess.handle_frame(OP_CLOSE, b"")
        self.assertTrue(sess.closed)
        self.assertEqual(server_frames(out)[0][0], OP_CLOSE)
        self.assertEqual(sess.poll(), [])  # closed sessions don't deliver

    def test_bad_json_errors(self):
        sess = _session(FakeDeps())
        msgs = text_messages(sess.handle_frame(OP_TEXT, b"{not json"))
        self.assertEqual(msgs[0]["category"], "bad_message")
        self.assertFalse(sess.closed)

    def test_bad_agent_bridge_json_errors_and_closes_the_protocol(self):
        sess = _session(
            FakeDeps(),
            participant_type="agent",
            client_type="agent_bridge",
        )

        frames = sess.handle_frame(OP_TEXT, b"{not json")

        self.assertEqual(text_messages(frames)[0]["category"], "bad_message")
        self.assertEqual([opcode for opcode, _ in server_frames(frames)], [OP_TEXT, OP_CLOSE])
        self.assertTrue(sess.closed)

    def test_unknown_op_errors(self):
        sess = _session(FakeDeps())
        msgs = text_messages(sess.handle_frame(OP_TEXT, json.dumps({"op": "frobnicate"}).encode()))
        self.assertEqual(msgs[0]["category"], "unknown_op")


if __name__ == "__main__":
    unittest.main()
