import json
import tempfile
import unittest
from pathlib import Path

from agentsassemble.meeting import run_demo_meeting
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

    def test_provider_public_dict_scrubs_endpoint_and_notes(self):
        provider = ProviderConfig(
            id="bridge",
            kind="remote_http_bridge",
            display_name="Bridge",
            endpoint="https://user:secret-pass@example.com:8777/run?token=secret-token&room=public",
            notes="Bearer secret-token for testing",
        )

        public = provider.public_dict()
        payload = json.dumps(public, ensure_ascii=False)

        self.assertNotIn("secret-pass", payload)
        self.assertNotIn("secret-token", payload)
        self.assertEqual(public["notes"], "<redacted>")
        self.assertEqual(public["endpoint"], "<redacted>")

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
