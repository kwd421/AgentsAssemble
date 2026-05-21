import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agentsassemble.meeting_events import (
    append_live_event,
    read_live_events,
    read_lobby_events,
    read_side_chat_events,
)


class MeetingEventsTests(unittest.TestCase):
    def test_limited_lobby_and_side_chat_reads_do_not_load_full_jsonl_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            lobby_path = root / "lobby.jsonl"
            side_path = root / "side_chat.jsonl"
            older = [
                {
                    "id": f"old-{index}",
                    "created_at": f"2026-05-17T11:{index:02d}:00+00:00",
                    "name": "guest",
                    "side": "mine",
                    "kind": "message",
                    "message": f"old {index} " + ("x" * 200),
                }
                for index in range(200)
            ]
            recent = [
                {
                    "id": f"new-{index}",
                    "created_at": f"2026-05-17T12:0{index}:00+00:00",
                    "name": "guest",
                    "side": "mine",
                    "kind": "message",
                    "message": f"new {index}",
                }
                for index in range(3)
            ]
            for path in (lobby_path, side_path):
                path.write_text(
                    "\n".join(
                        [
                            *(json.dumps(event, ensure_ascii=False) for event in older),
                            json.dumps(recent[0], ensure_ascii=False),
                            "{broken lobby json",
                            *(json.dumps(event, ensure_ascii=False) for event in recent[1:]),
                        ]
                    )
                    + "\n",
                    encoding="utf-8",
                )
            lobby_size = lobby_path.stat().st_size
            side_size = side_path.stat().st_size
            original_read_text = Path.read_text
            byte_reads: dict[str, int] = {}
            original_open = Path.open

            def read_text_guard(path, *args, **kwargs):
                if path.name in {"lobby.jsonl", "side_chat.jsonl"}:
                    raise AssertionError("limited room event reads must not load full JSONL files")
                return original_read_text(path, *args, **kwargs)

            def open_guard(path, *args, **kwargs):
                file = original_open(path, *args, **kwargs)
                mode = args[0] if args else kwargs.get("mode", "r")
                if path.name in {"lobby.jsonl", "side_chat.jsonl"} and "b" in mode:
                    return _CountingBinaryFile(file, path.name, byte_reads)
                return file

            with (
                patch("agentsassemble.meeting_events.JSONL_TAIL_BLOCK_BYTES", 256),
                patch.object(Path, "read_text", read_text_guard),
                patch.object(Path, "open", open_guard),
            ):
                lobby_events = read_lobby_events(lobby_path, limit=3)
                side_events = read_side_chat_events(side_path, limit=3)

        self.assertEqual([event["message"] for event in lobby_events], ["new 0", "new 1", "new 2"])
        self.assertEqual([event["message"] for event in side_events], ["new 0", "new 1", "new 2"])
        self.assertEqual({event["channel"] for event in side_events}, {"side_chat"})
        self.assertLess(byte_reads["lobby.jsonl"], lobby_size // 10)
        self.assertLess(byte_reads["side_chat.jsonl"], side_size // 10)

    def test_limited_live_event_reads_do_not_load_full_jsonl_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            meeting_dir = Path(temp_dir) / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            path = meeting_dir / "live_events.jsonl"
            older = [
                {
                    "id": f"old-{index}",
                    "created_at": f"2026-05-17T11:{index:02d}:00+00:00",
                    "kind": "status",
                    "content": f"old {index} " + ("x" * 200),
                }
                for index in range(200)
            ]
            recent = [
                {
                    "id": f"new-{index}",
                    "created_at": f"2026-05-17T12:0{index}:00+00:00",
                    "kind": "message",
                    "content": f"new {index}",
                }
                for index in range(4)
            ]
            path.write_text(
                "\n".join(
                    [
                        *(json.dumps(event, ensure_ascii=False) for event in older),
                        json.dumps(recent[0], ensure_ascii=False),
                        "{broken live json",
                        *(json.dumps(event, ensure_ascii=False) for event in recent[1:]),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            file_size = path.stat().st_size
            original_read_text = Path.read_text
            byte_reads: dict[str, int] = {}
            original_open = Path.open

            def read_text_guard(path, *args, **kwargs):
                if path.name == "live_events.jsonl":
                    raise AssertionError("limited live event reads must not load full JSONL files")
                return original_read_text(path, *args, **kwargs)

            def open_guard(path, *args, **kwargs):
                file = original_open(path, *args, **kwargs)
                mode = args[0] if args else kwargs.get("mode", "r")
                if path.name == "live_events.jsonl" and "b" in mode:
                    return _CountingBinaryFile(file, path.name, byte_reads)
                return file

            with (
                patch("agentsassemble.meeting_events.JSONL_TAIL_BLOCK_BYTES", 256),
                patch.object(Path, "read_text", read_text_guard),
                patch.object(Path, "open", open_guard),
            ):
                events = read_live_events(meeting_dir, limit=4)

        self.assertEqual([event["content"] for event in events], ["new 0", "new 1", "new 2", "new 3"])
        self.assertLess(byte_reads["live_events.jsonl"], file_size // 10)

    def test_full_live_event_read_preserves_complete_history(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            meeting_dir = Path(temp_dir) / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)
            path = meeting_dir / "live_events.jsonl"
            events = [
                {
                    "id": f"event-{index}",
                    "created_at": f"2026-05-17T12:{index:02d}:00+00:00",
                    "kind": "message",
                    "content": f"event {index}",
                }
                for index in range(6)
            ]
            path.write_text(
                "\n".join(
                    [
                        *(json.dumps(event, ensure_ascii=False) for event in events[:3]),
                        "{broken live json",
                        *(json.dumps(event, ensure_ascii=False) for event in events[3:]),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            result = read_live_events(meeting_dir, limit=None)

        self.assertEqual([event["id"] for event in result], [event["id"] for event in events])

    def test_append_live_event_preserves_artifact_metadata(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            meeting_dir = Path(temp_dir) / "meetings" / "m1"
            meeting_dir.mkdir(parents=True)

            event = append_live_event(
                meeting_dir,
                {
                    "kind": "artifact",
                    "artifact_kind": "return_packet",
                    "artifact_path": "return_packets/architect.md",
                    "artifact_json_path": "return_packets/architect.json",
                    "target_agent_id": "agent-a",
                    "audience": "agent:agent-a",
                },
            )

            self.assertEqual(event["artifact_kind"], "return_packet")
            self.assertEqual(event["artifact_path"], "return_packets/architect.md")
            self.assertEqual(event["artifact_json_path"], "return_packets/architect.json")


class _CountingBinaryFile:
    def __init__(self, file, name: str, byte_reads: dict[str, int]) -> None:
        self._file = file
        self._name = name
        self._byte_reads = byte_reads

    def __enter__(self):
        self._file.__enter__()
        return self

    def __exit__(self, *args):
        return self._file.__exit__(*args)

    def seek(self, *args):
        return self._file.seek(*args)

    def tell(self):
        return self._file.tell()

    def read(self, *args):
        data = self._file.read(*args)
        self._byte_reads[self._name] = self._byte_reads.get(self._name, 0) + len(data)
        return data


if __name__ == "__main__":
    unittest.main()
