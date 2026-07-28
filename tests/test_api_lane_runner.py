"""API-provider lane wired as a live-agent kind (connection_kind=api_call).

Proves the lane is a first-class resident: context contract, config build,
command-runner dispatch, in-process model call + usage recording, validation.
"""
import argparse
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agentsassemble import cli
from agentsassemble.gui import model_catalog_payload as cli_gui_model_catalog_payload
from agentsassemble.persistence.local.identity.repository import IdentityStore
from agentsassemble.legacy.live_agent.runtime.context import live_agent_context_contract
from agentsassemble.live_agent_runner import (
    SUPPORTED_RESIDENT_CONNECTION_KINDS,
    ResidentAgentConfig,
    config_from_args,
)


def _ok_body(content="hi", *, usage=True):
    data = {"choices": [{"message": {"content": content}}]}
    if usage:
        data["usage"] = {"prompt_tokens": 9, "completion_tokens": 4}
    return json.dumps(data).encode("utf-8")


def _poster(status, body):
    def post(url, body_bytes, headers, timeout):
        post.url = url
        return status, body
    return post


def _api_config(**over):
    base = dict(
        server="http://127.0.0.1:8765",
        agent_id="nvidia-bot",
        display_name="MiniMax",
        provider_kind="nvidia",
        connection_kind="api_call",
        session_id="",
        endpoint="",
        auth_ref="",
        meeting_id="room-1",
        engagement_mode="always",
        command=[],
        timeout_seconds=60,
        poll_interval=1.0,
        heartbeat_interval=30.0,
        cooldown=5.0,
        max_chain_depth=1,
        model_id="minimaxai/minimax-m2",
    )
    base.update(over)
    return ResidentAgentConfig(**base)


class ContextContractTests(unittest.TestCase):
    def test_api_call_maps_to_stateless_prompt_call(self):
        contract = live_agent_context_contract("nvidia", "api_call")
        self.assertEqual(contract["join_semantics"], "stateless_prompt_call")
        self.assertEqual(contract["context_durability"], "stateless_prompt")

    def test_api_call_is_a_supported_resident_kind(self):
        self.assertIn("api_call", SUPPORTED_RESIDENT_CONNECTION_KINDS)


class ConfigFromArgsTests(unittest.TestCase):
    def test_builds_api_call_config_with_model_and_key_source(self):
        args = argparse.Namespace(
            server="http://127.0.0.1:8765",
            agent_id="nvidia-bot",
            display_name="",
            provider_kind="nvidia",
            connection_kind="api_call",
            session_id="",
            endpoint="",
            auth_ref="",
            meeting_id="room-1",
            engagement_mode="always",
            model_id="minimaxai/minimax-m2",
            key_source="free",
            timeout=60,
            poll_interval=1.0,
            heartbeat_interval=30.0,
            cooldown=5.0,
            max_chain_depth=1,
            max_ticks=0,
        )
        config = config_from_args(args)
        self.assertEqual(config.connection_kind, "api_call")
        self.assertEqual(config.provider_kind, "nvidia")
        self.assertEqual(config.model_id, "minimaxai/minimax-m2")
        self.assertEqual(config.key_source, "free")
        self.assertEqual(config.command, [])  # no default command for api_call


class CommandRunnerDispatchTests(unittest.TestCase):
    def setUp(self):
        env = mock.patch.dict(os.environ, {"NVIDIA_API_KEY": "nv-key"})
        env.start()
        self.addCleanup(env.stop)

    def test_dispatch_returns_api_runner(self):
        runner = cli._command_runner_for_config(_api_config())
        self.assertIsInstance(runner, cli._ApiCatalogCommandRunner)

    def test_runner_calls_model_and_returns_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = _api_config(meeting_id="room-x", key_source="free")
            runner = cli._ApiCatalogCommandRunner(
                config, output_root=tmp, http_post=_poster(200, _ok_body("the reply"))
            )
            text = runner(["ignored"], "what's up", timeout_seconds=30)
            self.assertEqual(text, "the reply")
            # usage recorded to the local store
            store = IdentityStore(Path(tmp) / "identity.db")
            summary = store.usage_summary(meeting_id="room-x")
            self.assertEqual(summary["events"], 1)
            self.assertEqual(summary["input_tokens"], 9)

    def test_runner_without_output_root_still_replies(self):
        runner = cli._ApiCatalogCommandRunner(_api_config(), http_post=_poster(200, _ok_body("ok")))
        self.assertEqual(runner(["x"], "hi", timeout_seconds=30), "ok")

    def test_provider_error_becomes_runtime_error(self):
        runner = cli._ApiCatalogCommandRunner(_api_config(), http_post=_poster(429, b'{"e":"slow"}'))
        with self.assertRaises(RuntimeError) as ctx:
            runner(["x"], "hi", timeout_seconds=30)
        self.assertIn("rate_limit", str(ctx.exception))


class ModelCatalogPayloadTests(unittest.TestCase):
    def test_gui_model_catalog_payload_exposes_catalog_without_keys(self):
        with mock.patch.dict(os.environ, {"NVIDIA_API_KEY": "nv-supersecret"}):
            payload = cli_gui_model_catalog_payload()
        blob = repr(payload)
        self.assertNotIn("nv-supersecret", blob)
        self.assertIn("nvidia", payload["providers"])
        self.assertTrue(payload["providers"]["nvidia"]["key_present"])
        self.assertIn("fallback_chain", payload)


class GroupConfigMappingTests(unittest.TestCase):
    def test_api_call_agent_from_group_config_carries_model_and_key_source(self):
        # the real GUI launch path: a group config JSON -> load_group_configs
        from agentsassemble.live_agent_runner import load_group_configs

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "group.json"
            path.write_text(
                json.dumps(
                    {
                        "server": "http://127.0.0.1:8765",
                        "agents": [
                            {
                                "agent_id": "minimax-1",
                                "provider_kind": "nvidia",
                                "connection_kind": "api_call",
                                "model_id": "minimaxai/minimax-m2",
                                "key_source": "free",
                                "meeting_id": "room-1",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            configs = load_group_configs(path)
        self.assertEqual(len(configs), 1)
        config = configs[0]
        self.assertEqual(config.connection_kind, "api_call")
        self.assertEqual(config.model_id, "minimaxai/minimax-m2")
        self.assertEqual(config.key_source, "free")


class ValidationTests(unittest.TestCase):
    def test_known_provider_and_model_pass(self):
        self.assertIsNone(cli._validate_resident_config(_api_config()))

    def test_unknown_provider_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            cli._validate_resident_config(_api_config(provider_kind="nope"))
        self.assertIn("catalog provider", str(ctx.exception))

    def test_unknown_model_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            cli._validate_resident_config(_api_config(model_id="nope"))
        self.assertIn("--model", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
