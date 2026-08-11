import json
import tempfile
import unittest
from pathlib import Path

from agentsassemble.legacy.meeting.core.runner import run_demo_meeting
from agentsassemble.models import ProviderConfig


class PublicProviderArtifactTests(unittest.TestCase):
    def test_provider_public_dict_redacts_literal_auth_and_command(self):
        provider = ProviderConfig(
            id="friend",
            kind="local_cli",
            display_name="Friend CLI",
            auth_ref="literal:secret-token",
            command=["claude", "-p", "--api-key", "secret-token"],
        )

        public = provider.public_dict()
        payload = json.dumps(public, ensure_ascii=False)

        self.assertNotIn("secret-token", payload)
        self.assertEqual(public["auth_ref"], "literal:<redacted>")
        self.assertEqual(public["command"], ["<redacted>"])

    def test_provider_public_dict_redacts_unprefixed_raw_auth(self):
        provider = ProviderConfig(
            id="raw-token",
            kind="anthropic",
            display_name="Raw Token",
            auth_ref="sk-raw-secret",
        )

        public = provider.public_dict()

        self.assertEqual(public["auth_ref"], "<redacted>")

    def test_provider_public_dict_scrubs_isolated_endpoint_and_note_secrets(self):
        secret_cases = (
            {"endpoint": "https://example.com/run?token=token-value"},
            {"endpoint": "https://example.com/run?secret=secret-value"},
            {"endpoint": "https://example.com/run?authorization=auth-value"},
            {"endpoint": "https://example.com/run?password=password-value"},
            {"endpoint": "https://example.com/run?refresh_token=refresh-value"},
            {"endpoint": "https://user:user-password@example.com/run"},
            {"endpoint": "bearer endpoint-value"},
            {"notes": "Bearer note-value"},
            {"notes": "x-api-key: note-value"},
            {"notes": "token=note-value"},
            {"notes": "password=note-value"},
            {"notes": "client_secret=note-value"},
            {"notes": "AUTHORIZATION note-value"},
        )
        for fields in secret_cases:
            with self.subTest(fields=fields):
                provider = ProviderConfig(
                    id="bridge",
                    kind="remote_http_bridge",
                    display_name="Bridge",
                    **fields,
                )

                public = provider.public_dict()
                payload = json.dumps(public, ensure_ascii=False)

                self.assertNotIn(next(iter(fields.values())), payload)
                self.assertEqual(public[next(iter(fields))], "<redacted>")

    def test_provider_public_dict_scrubs_common_endpoint_key_params(self):
        for query in ("api_key=sk_live_abc123", "key=AIzaSyABC123", "access_key=abc123"):
            with self.subTest(query=query):
                provider = ProviderConfig(
                    id="bridge",
                    kind="remote_http_bridge",
                    display_name="Bridge",
                    endpoint=f"https://example.com/run?{query}&room=public",
                )

                public = provider.public_dict()
                payload = json.dumps(public, ensure_ascii=False)

                self.assertNotIn(query.split("=", 1)[1], payload)
                self.assertEqual(public["endpoint"], "<redacted>")

    def test_provider_public_dict_preserves_safe_metadata_and_env_auth_reference(self):
        provider = ProviderConfig(
            id="local-bridge",
            kind="remote_http_bridge",
            display_name="Local Bridge",
            endpoint="http://localhost:8080/v1?model=gpt-4&version=2",
            auth_ref="env:LOCAL_BRIDGE_API_KEY",
            notes="Uses a local model",
        )

        public = provider.public_dict()

        self.assertEqual(public["endpoint"], provider.endpoint)
        self.assertEqual(public["auth_ref"], provider.auth_ref)
        self.assertEqual(public["notes"], provider.notes)

    def test_meeting_artifacts_do_not_expose_provider_secrets(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            agent_config = root / "agents.json"
            agent_config.write_text(
                json.dumps(
                    {
                        "providers": [
                            {
                                "id": "secret-mock",
                                "kind": "mock",
                                "display_name": "Secret Mock",
                                "auth_ref": "literal:super-secret",
                                "command": ["mock", "--token", "super-secret"],
                            }
                        ],
                        "permission_profiles": [{"id": "read_only"}],
                        "agent_bindings": [
                            {
                                "agent_id": "a",
                                "role_id": "lore_lawyer",
                                "provider_id": "secret-mock",
                                "permission_profile_id": "read_only",
                            },
                            {
                                "agent_id": "b",
                                "role_id": "show_me_the_feats",
                                "provider_id": "secret-mock",
                                "permission_profile_id": "read_only",
                            },
                            {
                                "agent_id": "c",
                                "role_id": "fanboard_skeptic",
                                "provider_id": "secret-mock",
                                "permission_profile_id": "read_only",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )

            result = run_demo_meeting(adapter_name="mock", output_root=root, agent_config_path=agent_config)
            meeting_text = (result.meeting_dir / "meeting.json").read_text(encoding="utf-8")
            delegate_text = (result.meeting_dir / "delegate_packets" / "lore_lawyer.json").read_text(encoding="utf-8")

        self.assertNotIn("super-secret", meeting_text)
        self.assertNotIn("super-secret", delegate_text)


if __name__ == "__main__":
    unittest.main()
