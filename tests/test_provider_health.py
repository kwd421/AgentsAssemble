import json
import math
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from agentsassemble.provider_health import provider_health_report


class ProviderHealthTests(unittest.TestCase):
    def write_config(self, temp_dir, data):
        config_path = Path(temp_dir) / "agents.json"
        config_path.write_text(json.dumps(data), encoding="utf-8")
        return config_path

    def test_provider_health_reports_ok_without_running_commands_or_network(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self.write_config(
                temp_dir,
                {
                    "providers": [
                        {"id": "mock-provider", "kind": "mock", "display_name": "Mock"},
                        {
                            "id": "local-model",
                            "kind": "local_openai_compatible",
                            "display_name": "LM Studio",
                            "endpoint": "http://127.0.0.1:1234/v1",
                        },
                        {
                            "id": "cli-provider",
                            "kind": "local_cli",
                            "display_name": "Local CLI",
                            "command": ["fake-agent", "--json"],
                        },
                    ],
                    "permission_profiles": [
                        {"id": "meeting", "meeting_read": True, "official_turn": True}
                    ],
                    "agent_bindings": [
                        {
                            "agent_id": "mock-agent",
                            "role_id": "lore_lawyer",
                            "provider_id": "mock-provider",
                            "permission_profile_id": "meeting",
                        },
                        {
                            "agent_id": "cli-agent",
                            "role_id": "show_me_the_feats",
                            "provider_id": "cli-provider",
                            "permission_profile_id": "meeting",
                        },
                    ],
                },
            )
            resolved_commands = []

            def resolver(command):
                resolved_commands.append(command)
                return "/usr/local/bin/fake-agent" if command == "fake-agent" else None

            report = provider_health_report(config_path, command_resolver=resolver)

            self.assertEqual(report["status"], "ok")
            self.assertEqual(
                report["summary"],
                {
                    "providers": 3,
                    "failed_providers": 0,
                    "bindings": 2,
                    "failed_bindings": 0,
                    "checks_failed": 0,
                    "warnings": 0,
                },
            )
            self.assertEqual(resolved_commands, ["fake-agent"])
            providers = {provider["provider_id"]: provider for provider in report["providers"]}
            self.assertEqual(providers["local-model"]["status"], "ok")
            self.assertEqual(providers["cli-provider"]["command_path"], "/usr/local/bin/fake-agent")

    def test_provider_health_probe_none_does_not_call_probe_requester(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self.write_config(
                temp_dir,
                {
                    "providers": [
                        {
                            "id": "local-model",
                            "kind": "local_openai_compatible",
                            "display_name": "LM Studio",
                            "endpoint": "http://127.0.0.1:1234/v1",
                        }
                    ],
                    "permission_profiles": [{"id": "meeting"}],
                    "agent_bindings": [],
                },
            )

            def requester(url, timeout_seconds):
                raise AssertionError("probe_mode none must not call the network probe")

            report = provider_health_report(config_path, probe_requester=requester)

            self.assertEqual(report["status"], "ok")
            self.assertEqual(report["probe_mode"], "none")
            self.assertNotIn("local_probe", {check["id"] for check in report["providers"][0]["checks"]})

    def test_provider_health_local_probe_checks_loopback_openai_models_endpoint(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self.write_config(
                temp_dir,
                {
                    "providers": [
                        {
                            "id": "local-model",
                            "kind": "local_openai_compatible",
                            "display_name": "LM Studio",
                            "endpoint": "http://127.0.0.1:1234/v1",
                        }
                    ],
                    "permission_profiles": [{"id": "meeting"}],
                    "agent_bindings": [],
                },
            )
            calls = []

            def requester(url, timeout_seconds):
                calls.append({"url": url, "timeout_seconds": timeout_seconds})
                return {"data": [{"id": "gemma-local"}]}

            report = provider_health_report(
                config_path,
                probe_mode="local",
                probe_requester=requester,
                probe_timeout_seconds=0.75,
            )

            self.assertEqual(report["status"], "ok")
            self.assertEqual(report["probe_mode"], "local")
            self.assertEqual(calls, [{"url": "http://127.0.0.1:1234/v1/models", "timeout_seconds": 0.75}])
            self.assertIn(
                {
                    "id": "local_probe",
                    "status": "ok",
                    "message": "Local OpenAI-compatible models endpoint is reachable.",
                    "models": 1,
                },
                report["providers"][0]["checks"],
            )

    def test_provider_health_local_probe_uses_default_openai_endpoint_when_endpoint_missing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self.write_config(
                temp_dir,
                {
                    "providers": [
                        {
                            "id": "local-model",
                            "kind": "local_openai_compatible",
                            "display_name": "LM Studio",
                        }
                    ],
                    "permission_profiles": [{"id": "meeting"}],
                    "agent_bindings": [],
                },
            )
            calls = []

            def requester(url, timeout_seconds):
                calls.append(url)
                return {"data": [{"id": "local"}]}

            report = provider_health_report(config_path, probe_mode="local", probe_requester=requester)

            self.assertEqual(report["status"], "ok")
            self.assertEqual(calls, ["http://127.0.0.1:1234/v1/models"])

    def test_provider_health_rejects_invalid_probe_timeout(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self.write_config(
                temp_dir,
                {
                    "providers": [
                        {
                            "id": "local-model",
                            "kind": "local_openai_compatible",
                            "display_name": "LM Studio",
                        }
                    ],
                    "permission_profiles": [{"id": "meeting"}],
                    "agent_bindings": [],
                },
            )

            for timeout in [-1.0, math.inf, math.nan, "not-a-number"]:
                with self.subTest(timeout=timeout):
                    with self.assertRaisesRegex(ValueError, "probe_timeout_seconds"):
                        provider_health_report(
                            config_path,
                            probe_mode="local",
                            probe_requester=lambda url, timeout_seconds: {"data": [{"id": "local"}]},
                            probe_timeout_seconds=timeout,
                        )

    def test_provider_health_local_probe_normalizes_trailing_slash_endpoint(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self.write_config(
                temp_dir,
                {
                    "providers": [
                        {
                            "id": "local-model",
                            "kind": "local_openai_compatible",
                            "display_name": "LM Studio",
                            "endpoint": "http://localhost:1234/v1/",
                        }
                    ],
                    "permission_profiles": [{"id": "meeting"}],
                    "agent_bindings": [],
                },
            )
            calls = []

            def requester(url, timeout_seconds):
                calls.append(url)
                return {"data": [{"id": "local"}]}

            report = provider_health_report(config_path, probe_mode="local", probe_requester=requester)

            self.assertEqual(report["status"], "ok")
            self.assertEqual(calls, ["http://localhost:1234/v1/models"])

    def test_provider_health_local_probe_allows_loopback_ip_addresses(self):
        endpoints = [
            ("http://127.0.0.2:1234/v1", "http://127.0.0.2:1234/v1/models"),
            ("http://[::1]:1234/v1", "http://[::1]:1234/v1/models"),
        ]
        for endpoint, expected_url in endpoints:
            with self.subTest(endpoint=endpoint):
                with tempfile.TemporaryDirectory() as temp_dir:
                    config_path = self.write_config(
                        temp_dir,
                        {
                            "providers": [
                                {
                                    "id": "local-model",
                                    "kind": "local_openai_compatible",
                                    "display_name": "LM Studio",
                                    "endpoint": endpoint,
                                }
                            ],
                            "permission_profiles": [{"id": "meeting"}],
                            "agent_bindings": [],
                        },
                    )
                    calls = []

                    def requester(url, timeout_seconds):
                        calls.append(url)
                        return {"data": [{"id": "local"}]}

                    report = provider_health_report(config_path, probe_mode="local", probe_requester=requester)

                    self.assertEqual(report["status"], "ok")
                    self.assertEqual(calls, [expected_url])

    def test_provider_health_local_probe_skips_non_local_probe_provider_kinds(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self.write_config(
                temp_dir,
                {
                    "providers": [
                        {
                            "id": "claude",
                            "kind": "anthropic",
                            "display_name": "Claude API",
                            "auth_ref": "literal:claude-token",
                        },
                        {
                            "id": "bridge",
                            "kind": "remote_http_bridge",
                            "display_name": "Friend Bridge",
                            "endpoint": "http://192.0.2.10:8777",
                            "auth_ref": "literal:bridge-token",
                        },
                        {
                            "id": "cli",
                            "kind": "local_cli",
                            "display_name": "Local CLI",
                            "command": ["fake-agent"],
                        },
                    ],
                    "permission_profiles": [{"id": "meeting"}],
                    "agent_bindings": [],
                },
            )
            calls = []

            def requester(url, timeout_seconds):
                calls.append(url)
                return {"data": []}

            report = provider_health_report(
                config_path,
                command_resolver=lambda command: "/usr/local/bin/fake-agent",
                probe_mode="local",
                probe_requester=requester,
            )

            self.assertEqual(report["status"], "ok")
            self.assertEqual(calls, [])
            providers = {provider["provider_id"]: provider for provider in report["providers"]}
            for provider_id in ["claude", "bridge", "cli"]:
                self.assertIn(
                    {
                        "id": "local_probe",
                        "status": "ok",
                        "message": "Local probe is not applicable for this provider kind.",
                    },
                    providers[provider_id]["checks"],
                )

    def test_provider_health_bridge_probe_checks_remote_bridge_health_without_running_bridge(self):
        requests = []

        class BridgeHealthHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append({"method": "GET", "path": self.path, "auth": self.headers.get("Authorization")})
                if self.path != "/agentsassemble/health":
                    self.send_response(404)
                    self.end_headers()
                    return
                if self.headers.get("Authorization") != "Bearer bridge-token":
                    self.send_response(401)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(
                    b'{"status":"ok","bridge":"claude_code","health_endpoint":"/agentsassemble/health","run_endpoint":"/agentsassemble/run"}'
                )

            def do_POST(self):
                requests.append({"method": "POST", "path": self.path, "auth": self.headers.get("Authorization")})
                self.send_response(500)
                self.end_headers()

            def log_message(self, format, *args):
                return

        with tempfile.TemporaryDirectory() as temp_dir:
            server = ThreadingHTTPServer(("127.0.0.1", 0), BridgeHealthHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                config_path = self.write_config(
                    temp_dir,
                    {
                        "providers": [
                            {
                                "id": "friend-bridge",
                                "kind": "remote_http_bridge",
                                "display_name": "Friend Bridge",
                                "endpoint": f"http://127.0.0.1:{server.server_port}",
                                "auth_ref": "literal:bridge-token",
                            }
                        ],
                        "permission_profiles": [{"id": "meeting"}],
                        "agent_bindings": [],
                    },
                )

                report = provider_health_report(config_path, probe_mode="bridge")
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["probe_mode"], "bridge")
        self.assertEqual(requests, [{"method": "GET", "path": "/agentsassemble/health", "auth": "Bearer bridge-token"}])
        self.assertNotIn("bridge-token", json.dumps(report))
        self.assertIn(
            {
                "id": "bridge_probe",
                "status": "ok",
                "message": "Remote bridge health endpoint is reachable.",
            },
            report["providers"][0]["checks"],
        )

    def test_provider_health_bridge_probe_requires_available_auth_without_calling_endpoint(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self.write_config(
                temp_dir,
                {
                    "providers": [
                        {
                            "id": "friend-bridge",
                            "kind": "remote_http_bridge",
                            "display_name": "Friend Bridge",
                            "endpoint": "http://127.0.0.1:8777",
                            "auth_ref": "env:AGENTSASSEMBLE_TEST_MISSING_BRIDGE_TOKEN",
                        }
                    ],
                    "permission_profiles": [{"id": "meeting"}],
                    "agent_bindings": [],
                },
            )

            def requester(url, headers, timeout_seconds):
                raise AssertionError("bridge probe must not call without an available auth_ref")

            report = provider_health_report(config_path, probe_mode="bridge", bridge_probe_requester=requester)

        self.assertEqual(report["status"], "failed")
        self.assertIn(
            {
                "id": "bridge_probe",
                "status": "failed",
                "message": "Bridge probe requires an available auth_ref.",
            },
            report["providers"][0]["checks"],
        )

    def test_provider_health_bridge_probe_treats_redacted_auth_placeholder_as_missing_without_calling_endpoint(self):
        for auth_ref in ["literal:<redacted>", "<redacted>"]:
            with self.subTest(auth_ref=auth_ref):
                with tempfile.TemporaryDirectory() as temp_dir:
                    config_path = self.write_config(
                        temp_dir,
                        {
                            "providers": [
                                {
                                    "id": "friend-bridge",
                                    "kind": "remote_http_bridge",
                                    "display_name": "Friend Bridge",
                                    "endpoint": "http://127.0.0.1:8777",
                                    "auth_ref": auth_ref,
                                }
                            ],
                            "permission_profiles": [{"id": "meeting"}],
                            "agent_bindings": [],
                        },
                    )

                    def requester(url, headers, timeout_seconds):
                        raise AssertionError("bridge probe must not call with redacted placeholder auth")

                    report = provider_health_report(config_path, probe_mode="bridge", bridge_probe_requester=requester)

                self.assertEqual(report["status"], "failed")
                self.assertIn(
                    {
                        "id": "bridge_probe",
                        "status": "failed",
                        "message": "Bridge probe requires an available auth_ref.",
                    },
                    report["providers"][0]["checks"],
                )

    def test_provider_health_bridge_probe_skips_non_bridge_provider_kinds(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self.write_config(
                temp_dir,
                {
                    "providers": [
                        {"id": "mock-provider", "kind": "mock", "display_name": "Mock"},
                        {
                            "id": "local-model",
                            "kind": "local_openai_compatible",
                            "display_name": "LM Studio",
                            "endpoint": "http://127.0.0.1:1234/v1",
                        },
                    ],
                    "permission_profiles": [{"id": "meeting"}],
                    "agent_bindings": [],
                },
            )

            def requester(url, headers, timeout_seconds):
                raise AssertionError("bridge probe must not call non-bridge providers")

            report = provider_health_report(config_path, probe_mode="bridge", bridge_probe_requester=requester)

            self.assertEqual(report["status"], "ok")
            for provider in report["providers"]:
                self.assertIn(
                    {
                        "id": "bridge_probe",
                        "status": "ok",
                        "message": "Bridge probe is not applicable for this provider kind.",
                    },
                    provider["checks"],
                )

    def test_provider_health_bridge_probe_rejects_disallowed_endpoint_without_leaking_or_calling(self):
        disallowed_endpoints = [
            "http://user:super-secret@example.test:8777",
            "http://example.test:8777?token=super-secret",
            "http://example.test:8777#super-secret",
        ]
        for endpoint in disallowed_endpoints:
            with self.subTest(endpoint=endpoint):
                with tempfile.TemporaryDirectory() as temp_dir:
                    config_path = self.write_config(
                        temp_dir,
                        {
                            "providers": [
                                {
                                    "id": "friend-bridge",
                                    "kind": "remote_http_bridge",
                                    "display_name": "Friend Bridge",
                                    "endpoint": endpoint,
                                    "auth_ref": "literal:bridge-token",
                                }
                            ],
                            "permission_profiles": [{"id": "meeting"}],
                            "agent_bindings": [],
                        },
                    )

                    def requester(url, headers, timeout_seconds):
                        raise AssertionError("bridge probe must not call disallowed endpoints")

                    report = provider_health_report(
                        config_path,
                        probe_mode="bridge",
                        bridge_probe_requester=requester,
                    )

                    self.assertEqual(report["status"], "failed")
                    self.assertNotIn("super-secret", json.dumps(report))
                    self.assertNotIn("bridge-token", json.dumps(report))
                    self.assertIn(
                        {
                            "id": "bridge_probe",
                            "status": "failed",
                            "message": "Bridge probe requires an HTTP or HTTPS endpoint without userinfo, query, or fragment.",
                        },
                        report["providers"][0]["checks"],
                    )

    def test_provider_health_bridge_probe_does_not_follow_redirects_to_run_endpoint(self):
        paths = []

        class RedirectBridgeHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                paths.append(self.path)
                if self.path == "/agentsassemble/health":
                    self.send_response(302)
                    self.send_header("Location", "/agentsassemble/run")
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status":"ok"}')

            def do_POST(self):
                paths.append(self.path)
                self.send_response(500)
                self.end_headers()

            def log_message(self, format, *args):
                return

        with tempfile.TemporaryDirectory() as temp_dir:
            server = ThreadingHTTPServer(("127.0.0.1", 0), RedirectBridgeHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                config_path = self.write_config(
                    temp_dir,
                    {
                        "providers": [
                            {
                                "id": "friend-bridge",
                                "kind": "remote_http_bridge",
                                "display_name": "Friend Bridge",
                                "endpoint": f"http://127.0.0.1:{server.server_port}",
                                "auth_ref": "literal:bridge-token",
                            }
                        ],
                        "permission_profiles": [{"id": "meeting"}],
                        "agent_bindings": [],
                    },
                )

                report = provider_health_report(config_path, probe_mode="bridge")
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(report["status"], "failed")
        self.assertEqual(paths, ["/agentsassemble/health"])

    def test_provider_health_bridge_probe_reports_auth_rejection_without_leaking_token(self):
        requests = []

        class UnauthorizedBridgeHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append({"path": self.path, "auth": self.headers.get("Authorization")})
                self.send_response(401)
                self.end_headers()

            def log_message(self, format, *args):
                return

        with tempfile.TemporaryDirectory() as temp_dir:
            server = ThreadingHTTPServer(("127.0.0.1", 0), UnauthorizedBridgeHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                config_path = self.write_config(
                    temp_dir,
                    {
                        "providers": [
                            {
                                "id": "friend-bridge",
                                "kind": "remote_http_bridge",
                                "display_name": "Friend Bridge",
                                "endpoint": f"http://127.0.0.1:{server.server_port}",
                                "auth_ref": "literal:wrong-token",
                            }
                        ],
                        "permission_profiles": [{"id": "meeting"}],
                        "agent_bindings": [],
                    },
                )

                report = provider_health_report(config_path, probe_mode="bridge")
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(report["status"], "failed")
        self.assertEqual(requests, [{"path": "/agentsassemble/health", "auth": "Bearer wrong-token"}])
        self.assertNotIn("wrong-token", json.dumps(report))
        self.assertIn(
            {
                "id": "bridge_probe",
                "status": "failed",
                "message": "Remote bridge health endpoint rejected authentication.",
            },
            report["providers"][0]["checks"],
        )

    def test_provider_health_bridge_probe_requires_health_contract_fields(self):
        class IncompleteBridgeHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status":"ok","bridge":"claude_code"}')

            def log_message(self, format, *args):
                return

        with tempfile.TemporaryDirectory() as temp_dir:
            server = ThreadingHTTPServer(("127.0.0.1", 0), IncompleteBridgeHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                config_path = self.write_config(
                    temp_dir,
                    {
                        "providers": [
                            {
                                "id": "friend-bridge",
                                "kind": "remote_http_bridge",
                                "display_name": "Friend Bridge",
                                "endpoint": f"http://127.0.0.1:{server.server_port}",
                                "auth_ref": "literal:bridge-token",
                            }
                        ],
                        "permission_profiles": [{"id": "meeting"}],
                        "agent_bindings": [],
                    },
                )

                report = provider_health_report(config_path, probe_mode="bridge")
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(report["status"], "failed")
        self.assertIn(
            {
                "id": "bridge_probe",
                "status": "failed",
                "message": "Remote bridge health endpoint did not return the expected bridge health contract.",
            },
            report["providers"][0]["checks"],
        )

    def test_provider_health_bridge_probe_ignores_environment_proxies(self):
        proxy_paths = []

        class ProxyHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                proxy_paths.append(self.path)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(
                    b'{"status":"ok","bridge":"claude_code","health_endpoint":"/agentsassemble/health","run_endpoint":"/agentsassemble/run"}'
                )

            def log_message(self, format, *args):
                return

        with tempfile.TemporaryDirectory() as temp_dir:
            proxy = ThreadingHTTPServer(("127.0.0.1", 0), ProxyHandler)
            thread = threading.Thread(target=proxy.serve_forever, daemon=True)
            thread.start()
            try:
                config_path = self.write_config(
                    temp_dir,
                    {
                        "providers": [
                            {
                                "id": "friend-bridge",
                                "kind": "remote_http_bridge",
                                "display_name": "Friend Bridge",
                                "endpoint": "http://127.0.0.2:9",
                                "auth_ref": "literal:bridge-token",
                            }
                        ],
                        "permission_profiles": [{"id": "meeting"}],
                        "agent_bindings": [],
                    },
                )

                with patch.dict(
                    "os.environ",
                    {
                        "HTTP_PROXY": f"http://127.0.0.1:{proxy.server_port}",
                        "http_proxy": f"http://127.0.0.1:{proxy.server_port}",
                        "NO_PROXY": "",
                        "no_proxy": "",
                    },
                    clear=False,
                ):
                    report = provider_health_report(config_path, probe_mode="bridge")
            finally:
                proxy.shutdown()
                proxy.server_close()

        self.assertEqual(report["status"], "failed")
        self.assertEqual(proxy_paths, [])

    def test_provider_health_local_probe_rejects_non_loopback_openai_endpoint_without_calling_it(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self.write_config(
                temp_dir,
                {
                    "providers": [
                        {
                            "id": "hosted-openai-compatible",
                            "kind": "local_openai_compatible",
                            "display_name": "Hosted OpenAI-Compatible",
                            "endpoint": "https://api.example.test/v1?token=secret",
                        }
                    ],
                    "permission_profiles": [{"id": "meeting"}],
                    "agent_bindings": [],
                },
            )

            def requester(url, timeout_seconds):
                raise AssertionError("local probe must not call non-loopback endpoints")

            report = provider_health_report(
                config_path,
                probe_mode="local",
                probe_requester=requester,
            )

            self.assertEqual(report["status"], "failed")
            report_text = json.dumps(report)
            self.assertNotIn("secret", report_text)
            self.assertIn(
                {
                    "id": "local_probe",
                    "status": "failed",
                    "message": "Local probe only allows loopback HTTP endpoints.",
                },
                report["providers"][0]["checks"],
            )

    def test_provider_health_local_probe_rejects_userinfo_query_and_fragment_without_leaking(self):
        disallowed_endpoints = [
            "http://user:super-secret@127.0.0.1:1234/v1",
            "http://127.0.0.1:1234/v1?token=super-secret",
            "http://127.0.0.1:1234/v1#super-secret",
            "https://127.0.0.1:1234/v1",
            "http://192.168.0.10:1234/v1",
            "http://0.0.0.0:1234/v1",
        ]
        for endpoint in disallowed_endpoints:
            with self.subTest(endpoint=endpoint):
                with tempfile.TemporaryDirectory() as temp_dir:
                    config_path = self.write_config(
                        temp_dir,
                        {
                            "providers": [
                                {
                                    "id": "hosted-openai-compatible",
                                    "kind": "local_openai_compatible",
                                    "display_name": "Hosted OpenAI-Compatible",
                                    "endpoint": endpoint,
                                }
                            ],
                            "permission_profiles": [{"id": "meeting"}],
                            "agent_bindings": [],
                        },
                    )

                    def requester(url, timeout_seconds):
                        raise AssertionError("local probe must not call disallowed endpoints")

                    report = provider_health_report(
                        config_path,
                        probe_mode="local",
                        probe_requester=requester,
                    )

                    self.assertEqual(report["status"], "failed")
                    self.assertNotIn("super-secret", json.dumps(report))
                    self.assertIn(
                        {
                            "id": "local_probe",
                            "status": "failed",
                            "message": "Local probe only allows loopback HTTP endpoints.",
                        },
                        report["providers"][0]["checks"],
                    )

    def test_provider_health_local_probe_reports_unreachable_or_malformed_models_response(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self.write_config(
                temp_dir,
                {
                    "providers": [
                        {
                            "id": "local-model",
                            "kind": "local_openai_compatible",
                            "display_name": "LM Studio",
                            "endpoint": "http://localhost:1234/v1",
                        }
                    ],
                    "permission_profiles": [{"id": "meeting"}],
                    "agent_bindings": [],
                },
            )

            def requester(url, timeout_seconds):
                return {"unexpected": []}

            report = provider_health_report(
                config_path,
                probe_mode="local",
                probe_requester=requester,
            )

            self.assertEqual(report["status"], "failed")
            self.assertIn(
                {
                    "id": "local_probe",
                    "status": "failed",
                    "message": "Local OpenAI-compatible models endpoint did not return a model list.",
                },
                report["providers"][0]["checks"],
            )

    def test_provider_health_local_probe_sanitizes_requester_exception_messages(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = self.write_config(
                temp_dir,
                {
                    "providers": [
                        {
                            "id": "local-model",
                            "kind": "local_openai_compatible",
                            "display_name": "LM Studio",
                            "endpoint": "http://127.0.0.1:1234/v1",
                        }
                    ],
                    "permission_profiles": [{"id": "meeting"}],
                    "agent_bindings": [],
                },
            )

            def requester(url, timeout_seconds):
                raise RuntimeError("connection failed with token super-secret")

            report = provider_health_report(config_path, probe_mode="local", probe_requester=requester)

            self.assertEqual(report["status"], "failed")
            self.assertNotIn("super-secret", json.dumps(report))
            self.assertIn(
                {
                    "id": "local_probe",
                    "status": "failed",
                    "message": "Local OpenAI-compatible models endpoint is unreachable.",
                },
                report["providers"][0]["checks"],
            )

    def test_default_local_probe_requester_does_not_follow_redirects_to_other_paths(self):
        paths = []

        class RedirectHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                paths.append(self.path)
                if self.path == "/v1/models":
                    self.send_response(302)
                    self.send_header("Location", "/chat/completions")
                    self.end_headers()
                    return
                if self.path == "/chat/completions":
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"data":[{"id":"wrong-path"}]}')
                    return
                self.send_response(404)
                self.end_headers()

            def log_message(self, format, *args):
                return

        with tempfile.TemporaryDirectory() as temp_dir:
            server = ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                config_path = self.write_config(
                    temp_dir,
                    {
                        "providers": [
                            {
                                "id": "local-model",
                                "kind": "local_openai_compatible",
                                "display_name": "LM Studio",
                                "endpoint": f"http://127.0.0.1:{server.server_port}/v1",
                            }
                        ],
                        "permission_profiles": [{"id": "meeting"}],
                        "agent_bindings": [],
                    },
                )

                with patch.dict("os.environ", {"NO_PROXY": "*", "no_proxy": "*"}, clear=False):
                    report = provider_health_report(config_path, probe_mode="local")
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(report["status"], "failed")
        self.assertEqual(paths, ["/v1/models"])

    def test_default_local_probe_requester_ignores_environment_proxies(self):
        proxy_paths = []

        class ProxyHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                proxy_paths.append(self.path)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"data":[{"id":"proxy-model"}]}')

            def log_message(self, format, *args):
                return

        with tempfile.TemporaryDirectory() as temp_dir:
            proxy = ThreadingHTTPServer(("127.0.0.1", 0), ProxyHandler)
            thread = threading.Thread(target=proxy.serve_forever, daemon=True)
            thread.start()
            try:
                config_path = self.write_config(
                    temp_dir,
                    {
                        "providers": [
                            {
                                "id": "local-model",
                                "kind": "local_openai_compatible",
                                "display_name": "LM Studio",
                                "endpoint": "http://127.0.0.2:9/v1",
                            }
                        ],
                        "permission_profiles": [{"id": "meeting"}],
                        "agent_bindings": [],
                    },
                )

                with patch.dict(
                    "os.environ",
                    {
                        "HTTP_PROXY": f"http://127.0.0.1:{proxy.server_port}",
                        "http_proxy": f"http://127.0.0.1:{proxy.server_port}",
                        "NO_PROXY": "",
                        "no_proxy": "",
                    },
                    clear=False,
                ):
                    report = provider_health_report(config_path, probe_mode="local")
            finally:
                proxy.shutdown()
                proxy.server_close()

        self.assertEqual(report["status"], "failed")
        self.assertEqual(proxy_paths, [])

    def test_provider_health_reports_missing_auth_planned_kinds_bad_commands_and_binding_errors(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "agents.json"
            config_path.write_text(
                json.dumps(
                    {
                        "providers": [
                            {
                                "id": "claude",
                                "kind": "anthropic",
                                "display_name": "Claude API",
                                "auth_ref": "env:AGENTSASSEMBLE_TEST_MISSING_KEY",
                            },
                            {
                                "id": "cursor",
                                "kind": "cursor",
                                "display_name": "Cursor",
                            },
                            {
                                "id": "bad-cli",
                                "kind": "local_cli",
                                "display_name": "Missing CLI",
                                "command": ["missing-agent"],
                            },
                        ],
                        "permission_profiles": [
                            {"id": "meeting", "meeting_read": True, "official_turn": True},
                            {"id": "unsafe", "implementation": True, "filesystem_write": True},
                        ],
                        "agent_bindings": [
                            {
                                "agent_id": "claude-agent",
                                "role_id": "lore_lawyer",
                                "provider_id": "claude",
                                "permission_profile_id": "meeting",
                            },
                            {
                                "agent_id": "cursor-agent",
                                "role_id": "show_me_the_feats",
                                "provider_id": "cursor",
                                "permission_profile_id": "unsafe",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )

            report = provider_health_report(config_path, command_resolver=lambda command: None)

            self.assertEqual(report["status"], "failed")
            self.assertEqual(report["summary"]["providers"], 3)
            self.assertEqual(report["summary"]["failed_providers"], 3)
            self.assertGreaterEqual(report["summary"]["failed_bindings"], 2)
            providers = {provider["provider_id"]: provider for provider in report["providers"]}
            self.assertIn(
                {
                    "id": "auth_ref",
                    "status": "failed",
                    "message": "Required auth_ref is not available.",
                },
                providers["claude"]["checks"],
            )
            self.assertIn(
                {
                    "id": "provider_kind",
                    "status": "failed",
                    "message": "Provider kind cursor is planned, not available for execution.",
                },
                providers["cursor"]["checks"],
            )
            self.assertIn(
                {"id": "command", "status": "failed", "message": "Command not found: missing-agent"},
                providers["bad-cli"]["checks"],
            )

    def test_provider_health_reports_duplicate_ids_and_secret_permissions(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "agents.json"
            config_path.write_text(
                json.dumps(
                    {
                        "providers": [
                            {"id": "dup-provider", "kind": "mock", "display_name": "Mock A"},
                            {"id": "dup-provider", "kind": "mock", "display_name": "Mock B"},
                        ],
                        "permission_profiles": [
                            {"id": "secret-meeting", "secrets": True},
                            {"id": "secret-meeting", "secrets": True},
                        ],
                        "agent_bindings": [
                            {
                                "agent_id": "dup-agent",
                                "role_id": "lore_lawyer",
                                "provider_id": "dup-provider",
                                "permission_profile_id": "secret-meeting",
                            },
                            {
                                "agent_id": "dup-agent",
                                "role_id": "lore_lawyer",
                                "provider_id": "dup-provider",
                                "permission_profile_id": "secret-meeting",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )

            report = provider_health_report(config_path)

            self.assertEqual(report["status"], "failed")
            self.assertIn(
                {"id": "provider_ids", "status": "failed", "message": "Duplicate provider ids: dup-provider"},
                report["checks"],
            )
            self.assertIn(
                {"id": "permission_ids", "status": "failed", "message": "Duplicate permission profile ids: secret-meeting"},
                report["checks"],
            )
            self.assertIn(
                {"id": "agent_ids", "status": "failed", "message": "Duplicate agent ids: dup-agent"},
                report["checks"],
            )
            self.assertIn(
                {"id": "role_bindings", "status": "failed", "message": "Duplicate role bindings: lore_lawyer"},
                report["checks"],
            )
            self.assertIn(
                {
                    "id": "secrets",
                    "status": "failed",
                    "message": "Agent dup-agent requests secret access during a meeting-only run.",
                },
                report["bindings"][0]["checks"],
            )

    def test_provider_health_does_not_leak_literal_auth_values(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "agents.json"
            config_path.write_text(
                json.dumps(
                    {
                        "providers": [
                            {
                                "id": "gemini",
                                "kind": "gemini",
                                "display_name": "Gemini",
                                "auth_ref": "literal:super-secret-provider-token",
                            }
                        ],
                        "permission_profiles": [{"id": "meeting"}],
                        "agent_bindings": [],
                    }
                ),
                encoding="utf-8",
            )

            report = provider_health_report(config_path)

            self.assertEqual(report["status"], "ok")
            self.assertNotIn("super-secret-provider-token", json.dumps(report))

    def test_provider_health_treats_redacted_auth_placeholder_as_unavailable(self):
        for auth_ref in ["literal:<redacted>", "<redacted>"]:
            with self.subTest(auth_ref=auth_ref):
                with tempfile.TemporaryDirectory() as temp_dir:
                    config_path = Path(temp_dir) / "agents.json"
                    config_path.write_text(
                        json.dumps(
                            {
                                "providers": [
                                    {
                                        "id": "gemini",
                                        "kind": "gemini",
                                        "display_name": "Gemini",
                                        "auth_ref": auth_ref,
                                    }
                                ],
                                "permission_profiles": [{"id": "meeting"}],
                                "agent_bindings": [],
                            }
                        ),
                        encoding="utf-8",
                    )

                    report = provider_health_report(config_path)

                self.assertEqual(report["status"], "failed")
                self.assertIn(
                    {
                        "id": "auth_ref",
                        "status": "failed",
                        "message": "Required auth_ref is not available.",
                    },
                    report["providers"][0]["checks"],
                )

    def test_provider_health_returns_failed_report_for_invalid_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "agents.json"
            config_path.write_text("[]", encoding="utf-8")

            report = provider_health_report(config_path)

            self.assertEqual(report["status"], "failed")
            self.assertEqual(report["summary"]["checks_failed"], 1)
            self.assertEqual(report["checks"][0]["id"], "config_load")

    def test_provider_health_checks_environment_auth_presence_without_revealing_value(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "agents.json"
            config_path.write_text(
                json.dumps(
                    {
                        "providers": [
                            {
                                "id": "grok",
                                "kind": "grok",
                                "display_name": "Grok",
                                "auth_ref": "env:AGENTSASSEMBLE_TEST_XAI_KEY",
                            }
                        ],
                        "permission_profiles": [{"id": "meeting"}],
                        "agent_bindings": [],
                    }
                ),
                encoding="utf-8",
            )

            with patch.dict("os.environ", {"AGENTSASSEMBLE_TEST_XAI_KEY": "secret-xai-value"}):
                report = provider_health_report(config_path)

            self.assertEqual(report["status"], "ok")
            self.assertNotIn("secret-xai-value", json.dumps(report))

    def test_provider_health_does_not_read_environment_secret_values(self):
        class PresenceOnlyEnv:
            def __contains__(self, key):
                return key == "AGENTSASSEMBLE_TEST_ANTHROPIC_KEY"

            def get(self, key, default=None):
                raise AssertionError("provider health must not read secret values")

            def __getitem__(self, key):
                raise AssertionError("provider health must not read secret values")

        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "agents.json"
            config_path.write_text(
                json.dumps(
                    {
                        "providers": [
                            {
                                "id": "claude",
                                "kind": "anthropic",
                                "display_name": "Claude",
                                "auth_ref": "env:AGENTSASSEMBLE_TEST_ANTHROPIC_KEY",
                            }
                        ],
                        "permission_profiles": [{"id": "meeting"}],
                        "agent_bindings": [],
                    }
                ),
                encoding="utf-8",
            )

            with patch("agentsassemble.provider_health.os.environ", PresenceOnlyEnv()):
                with patch("agentsassemble.adapters.http_llm.os.environ", PresenceOnlyEnv()):
                    report = provider_health_report(config_path)

            self.assertEqual(report["status"], "ok")

    def test_provider_health_does_not_construct_provider_adapters_for_binding_validation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "agents.json"
            config_path.write_text(
                json.dumps(
                    {
                        "providers": [
                            {
                                "id": "cli",
                                "kind": "local_cli",
                                "display_name": "Local CLI",
                                "command": ["fake-agent"],
                            }
                        ],
                        "permission_profiles": [{"id": "meeting"}],
                        "agent_bindings": [
                            {
                                "agent_id": "cli-agent",
                                "role_id": "lore_lawyer",
                                "provider_id": "cli",
                                "permission_profile_id": "meeting",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            with patch("agentsassemble.adapters.registry.LocalCliAdapter", side_effect=AssertionError("no adapter construction")):
                report = provider_health_report(config_path, command_resolver=lambda command: "/usr/local/bin/fake-agent")

            self.assertEqual(report["status"], "ok")

    def test_provider_health_reports_malformed_endpoint_and_auth_ref_types_without_crashing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "agents.json"
            config_path.write_text(
                json.dumps(
                    {
                        "providers": [
                            {
                                "id": "bridge",
                                "kind": "remote_http_bridge",
                                "display_name": "Bridge",
                                "endpoint": {"url": "http://example.test"},
                                "auth_ref": ["env:BRIDGE_TOKEN"],
                            }
                        ],
                        "permission_profiles": [{"id": "meeting"}],
                        "agent_bindings": [
                            {
                                "agent_id": "bridge-agent",
                                "role_id": "lore_lawyer",
                                "provider_id": "bridge",
                                "permission_profile_id": "meeting",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            report = provider_health_report(config_path)

            self.assertEqual(report["status"], "failed")
            provider = report["providers"][0]
            self.assertIn(
                {"id": "endpoint", "status": "failed", "message": "Endpoint must be a string."},
                provider["checks"],
            )

    def test_provider_health_reports_malformed_provider_kind_without_crashing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "agents.json"
            config_path.write_text(
                json.dumps(
                    {
                        "providers": [
                            {
                                "id": "bad-kind",
                                "kind": ["mock"],
                                "display_name": "Bad Kind",
                            }
                        ],
                        "permission_profiles": [{"id": "meeting"}],
                        "agent_bindings": [],
                    }
                ),
                encoding="utf-8",
            )

            report = provider_health_report(config_path)

            self.assertEqual(report["status"], "failed")
            self.assertIn(
                {"id": "provider_kind", "status": "failed", "message": "Provider kind must be a string."},
                report["providers"][0]["checks"],
            )

    def test_provider_health_reports_malformed_binding_ids_without_crashing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "agents.json"
            config_path.write_text(
                json.dumps(
                    {
                        "providers": [{"id": "mock-provider", "kind": "mock", "display_name": "Mock"}],
                        "permission_profiles": [{"id": "meeting"}],
                        "agent_bindings": [
                            {
                                "agent_id": "bad-binding",
                                "role_id": "lore_lawyer",
                                "provider_id": ["mock-provider"],
                                "permission_profile_id": ["meeting"],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            report = provider_health_report(config_path)

            self.assertEqual(report["status"], "failed")
            binding = report["bindings"][0]
            self.assertIn(
                {"id": "provider_defined", "status": "failed", "message": "Provider id must be a string."},
                binding["checks"],
            )
            self.assertIn(
                {"id": "permission_defined", "status": "failed", "message": "Permission profile id must be a string."},
                binding["checks"],
            )


if __name__ == "__main__":
    unittest.main()
