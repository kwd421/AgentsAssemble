import unittest

from agentsassemble.providers.codex_app_server_live import CodexAppServerLiveRuntime


class _CompactingAppServer:
    def send_turn(self, handle, packet):
        del handle, packet
        return iter(
            [
                {"type": "context_compaction_started"},
                {"type": "context_compaction_finished"},
                {"type": "message_final", "content": "hello"},
            ]
        )

    def diagnose(self, handle):
        del handle
        return {"observed_model_id": "gpt-test"}


class ProviderCompactionActivityTests(unittest.TestCase):
    def test_codex_runtime_surfaces_compaction_during_an_active_turn(self):
        runtime = CodexAppServerLiveRuntime(
            "codex-guest",
            workspace="/tmp/room",
            model="gpt-test",
            reasoning_effort="low",
            permission_mode="meeting_read_only",
        )
        runtime.runtime = _CompactingAppServer()
        runtime.pending = "hello"
        activities = []

        result = runtime.read_output(
            timeout_seconds=2,
            on_activity=activities.append,
        )

        self.assertEqual(result["content"], "hello")
        self.assertEqual(
            activities,
            [
                {"category": "compaction", "status": "started"},
                {"category": "compaction", "status": "completed"},
            ],
        )


if __name__ == "__main__":
    unittest.main()
