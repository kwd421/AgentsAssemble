import unittest

from agentsassemble.models import (
    AgentBinding,
    CouncilConfig,
    MeetingRound,
    ModeratorConfig,
    PermissionProfile,
    ProviderCapabilities,
    ProviderConfig,
    RESEARCH_DEPTHS,
    ResearchDepth,
    ResearchSteering,
    Role,
    RoundTurnControl,
    _looks_sensitive,
    _public_auth_ref,
    _public_endpoint,
    _public_notes,
    _public_query_pair,
    get_research_depth,
    normalize_engagement_mode,
    normalize_meeting_mode,
)


class TestLooksSensitive(unittest.TestCase):
    def test_detects_bearer(self):
        self.assertTrue(_looks_sensitive("Bearer abc123"))

    def test_detects_api_key_variants(self):
        self.assertTrue(_looks_sensitive("x-api-key: foo"))
        self.assertTrue(_looks_sensitive("some api_key here"))
        self.assertTrue(_looks_sensitive("apikey=xyz"))

    def test_detects_token(self):
        self.assertTrue(_looks_sensitive("my token value"))

    def test_detects_password(self):
        self.assertTrue(_looks_sensitive("password=hunter2"))

    def test_detects_secret(self):
        self.assertTrue(_looks_sensitive("client_secret=abc"))

    def test_safe_value_passes(self):
        self.assertFalse(_looks_sensitive("https://example.com/api/v1"))
        self.assertFalse(_looks_sensitive("hello world"))

    def test_case_insensitive(self):
        self.assertTrue(_looks_sensitive("AUTHORIZATION header"))


class TestPublicAuthRef(unittest.TestCase):
    def test_none_returns_none(self):
        self.assertIsNone(_public_auth_ref(None))

    def test_env_ref_preserved(self):
        self.assertEqual(_public_auth_ref("env:MY_API_KEY"), "env:MY_API_KEY")

    def test_literal_redacted(self):
        self.assertEqual(_public_auth_ref("literal:sk-abc123"), "literal:<redacted>")

    def test_unknown_format_redacted(self):
        self.assertEqual(_public_auth_ref("sk-abc123456"), "<redacted>")

    def test_empty_string_redacted(self):
        self.assertEqual(_public_auth_ref(""), "<redacted>")


class TestPublicEndpoint(unittest.TestCase):
    def test_none_returns_none(self):
        self.assertIsNone(_public_endpoint(None))

    def test_safe_url_preserved(self):
        self.assertEqual(_public_endpoint("https://api.example.com/v1"), "https://api.example.com/v1")

    def test_url_with_api_key_query_redacted(self):
        result = _public_endpoint("https://api.example.com/v1?api_key=secret123")
        self.assertIn("<redacted>", result)
        self.assertNotIn("secret123", result)

    def test_url_with_token_query_redacted(self):
        result = _public_endpoint("https://api.example.com?token=abc")
        self.assertIn("<redacted>", result)
        self.assertNotIn("abc", result)

    def test_url_with_user_password_redacted(self):
        result = _public_endpoint("https://user:pass@example.com/path")
        self.assertEqual(result, "<redacted>")

    def test_non_url_with_sensitive_content_redacted(self):
        self.assertEqual(_public_endpoint("bearer token here"), "<redacted>")

    def test_non_url_safe_preserved(self):
        # No scheme/netloc, not sensitive
        self.assertEqual(_public_endpoint("localhost:8080"), "localhost:8080")

    def test_url_with_port_preserved(self):
        self.assertEqual(_public_endpoint("http://localhost:8080/api"), "http://localhost:8080/api")

    def test_safe_query_params_preserved(self):
        self.assertEqual(
            _public_endpoint("https://api.example.com/v1?model=gpt4&version=2"),
            "https://api.example.com/v1?model=gpt4&version=2",
        )


class TestPublicQueryPair(unittest.TestCase):
    def test_safe_pair_unchanged(self):
        key, value, sensitive = _public_query_pair("model", "gpt-4")
        self.assertEqual(key, "model")
        self.assertEqual(value, "gpt-4")
        self.assertFalse(sensitive)

    def test_sensitive_key_redacts_value(self):
        key, value, sensitive = _public_query_pair("api_key", "sk-abc")
        self.assertEqual(key, "api_key")
        self.assertEqual(value, "<redacted>")
        self.assertTrue(sensitive)

    def test_sensitive_value_redacted(self):
        key, value, sensitive = _public_query_pair("data", "bearer xyz")
        self.assertEqual(key, "data")
        self.assertEqual(value, "<redacted>")
        self.assertTrue(sensitive)

    def test_key_variants(self):
        for k in ("token", "secret", "authorization", "password", "access_key"):
            _, value, sensitive = _public_query_pair(k, "anything")
            self.assertEqual(value, "<redacted>", f"key={k} should be sensitive")
            self.assertTrue(sensitive)

    def test_key_ending_with_token(self):
        _, value, sensitive = _public_query_pair("refresh_token", "val")
        self.assertEqual(value, "<redacted>")
        self.assertTrue(sensitive)


class TestPublicNotes(unittest.TestCase):
    def test_none_returns_none(self):
        self.assertIsNone(_public_notes(None))

    def test_safe_notes_preserved(self):
        self.assertEqual(_public_notes("Uses local model"), "Uses local model")

    def test_sensitive_notes_redacted(self):
        self.assertEqual(_public_notes("api_key is stored in vault"), "<redacted>")


class TestNormalizeMeetingMode(unittest.TestCase):
    def test_debate_passes(self):
        self.assertEqual(normalize_meeting_mode("debate"), "debate")

    def test_free_chat_passes(self):
        self.assertEqual(normalize_meeting_mode("free_chat"), "free_chat")

    def test_hyphenated_free_chat_normalized(self):
        self.assertEqual(normalize_meeting_mode("free-chat"), "free_chat")

    def test_unknown_returns_default(self):
        self.assertEqual(normalize_meeting_mode("unknown"), "debate")

    def test_none_returns_default(self):
        self.assertEqual(normalize_meeting_mode(None), "debate")

    def test_custom_default(self):
        self.assertEqual(normalize_meeting_mode("garbage", "free_chat"), "free_chat")


class TestNormalizeEngagementMode(unittest.TestCase):
    def test_valid_modes_pass(self):
        for mode in ("manual", "mentioned", "moderator_called", "human_only", "always", "watch", "flow"):
            self.assertEqual(normalize_engagement_mode(mode), mode)

    def test_unknown_returns_default(self):
        self.assertEqual(normalize_engagement_mode("bogus"), "manual")

    def test_none_returns_default(self):
        self.assertEqual(normalize_engagement_mode(None), "manual")

    def test_custom_default(self):
        self.assertEqual(normalize_engagement_mode("nope", "watch"), "watch")


class TestGetResearchDepth(unittest.TestCase):
    def test_valid_names(self):
        for name in ("smoke", "standard", "deep"):
            depth = get_research_depth(name)
            self.assertIsInstance(depth, ResearchDepth)
            self.assertEqual(depth.name, name)

    def test_unknown_raises_value_error(self):
        with self.assertRaises(ValueError) as ctx:
            get_research_depth("ultra")
        self.assertIn("ultra", str(ctx.exception))
        self.assertIn("smoke", str(ctx.exception))

    def test_smoke_has_fewer_sources_than_standard(self):
        self.assertLess(RESEARCH_DEPTHS["smoke"].min_sources, RESEARCH_DEPTHS["standard"].min_sources)

    def test_deep_has_more_sources_than_standard(self):
        self.assertGreater(RESEARCH_DEPTHS["deep"].min_sources, RESEARCH_DEPTHS["standard"].min_sources)


class TestProviderConfigPublicDict(unittest.TestCase):
    def test_basic_public_dict(self):
        pc = ProviderConfig(id="p1", kind="mock", display_name="Mock Provider")
        d = pc.public_dict()
        self.assertEqual(d["id"], "p1")
        self.assertEqual(d["kind"], "mock")
        self.assertIsNone(d["endpoint"])
        self.assertIsNone(d["auth_ref"])
        self.assertIsNone(d["command"])
        self.assertFalse(d["command_configured"])

    def test_auth_ref_literal_redacted(self):
        pc = ProviderConfig(id="p1", kind="codex", display_name="Codex", auth_ref="literal:sk-secret")
        d = pc.public_dict()
        self.assertEqual(d["auth_ref"], "literal:<redacted>")
        self.assertNotIn("sk-secret", str(d))

    def test_auth_ref_env_preserved(self):
        pc = ProviderConfig(id="p1", kind="codex", display_name="Codex", auth_ref="env:OPENAI_API_KEY")
        d = pc.public_dict()
        self.assertEqual(d["auth_ref"], "env:OPENAI_API_KEY")

    def test_command_redacted(self):
        pc = ProviderConfig(id="p1", kind="local_cli", display_name="CLI", command=["codex", "exec", "--secret"])
        d = pc.public_dict()
        self.assertEqual(d["command"], ["<redacted>"])
        self.assertTrue(d["command_configured"])
        self.assertNotIn("codex", str(d["command"]))

    def test_endpoint_with_secret_query_redacted(self):
        pc = ProviderConfig(
            id="p1", kind="local_openai_compatible", display_name="Local",
            endpoint="https://api.example.com?api_key=sk-123"
        )
        d = pc.public_dict()
        self.assertNotIn("sk-123", str(d["endpoint"]))

    def test_sensitive_notes_redacted(self):
        pc = ProviderConfig(id="p1", kind="mock", display_name="M", notes="token is abc123")
        d = pc.public_dict()
        self.assertEqual(d["notes"], "<redacted>")

    def test_safe_notes_preserved(self):
        pc = ProviderConfig(id="p1", kind="mock", display_name="M", notes="Uses local model")
        d = pc.public_dict()
        self.assertEqual(d["notes"], "Uses local model")


class TestProviderCapabilitiesToDict(unittest.TestCase):
    def test_round_trip(self):
        caps = ProviderCapabilities(
            supports_research=True, supports_web_search=False, supports_tools=True,
            supports_filesystem=True, supports_session_resume=False,
            supports_structured_output=True, context_window=128000,
            cost_class="low", sandbox_enforcement="codex_readonly",
        )
        d = caps.to_dict()
        self.assertEqual(d["supports_research"], True)
        self.assertEqual(d["context_window"], 128000)
        self.assertEqual(d["cost_class"], "low")
        self.assertEqual(d["sandbox_enforcement"], "codex_readonly")

    def test_defaults(self):
        caps = ProviderCapabilities(
            supports_research=False, supports_web_search=False, supports_tools=False,
            supports_filesystem=False, supports_session_resume=False,
            supports_structured_output=False,
        )
        d = caps.to_dict()
        self.assertIsNone(d["context_window"])
        self.assertEqual(d["cost_class"], "unknown")
        self.assertEqual(d["sandbox_enforcement"], "advisory")


class TestAgentBindingToDict(unittest.TestCase):
    def test_minimal_binding(self):
        ab = AgentBinding(
            agent_id="a1", role_id="r1", owner_id="host",
            provider_id="mock", model_id=None, permission_profile_id="read_only",
        )
        d = ab.to_dict()
        self.assertEqual(d["agent_id"], "a1")
        self.assertEqual(d["engagement_mode"], "moderator_called")
        self.assertNotIn("persona_card_id", d)
        self.assertNotIn("character_mode", d)
        self.assertNotIn("first_message_index", d)
        self.assertNotIn("persona_variables", d)

    def test_persona_fields_included_when_set(self):
        ab = AgentBinding(
            agent_id="a1", role_id="r1", owner_id="host",
            provider_id="mock", model_id="gpt-4", permission_profile_id="rw",
            persona_card_id="card1", character_mode="on",
            first_message_index=3, persona_variables={"name": "Test"},
        )
        d = ab.to_dict()
        self.assertEqual(d["persona_card_id"], "card1")
        self.assertEqual(d["character_mode"], "on")
        self.assertEqual(d["first_message_index"], 3)
        self.assertEqual(d["persona_variables"], {"name": "Test"})

    def test_character_mode_included_when_non_off(self):
        ab = AgentBinding(
            agent_id="a1", role_id="r1", owner_id="host",
            provider_id="mock", model_id=None, permission_profile_id="ro",
            character_mode="work_speech_only",
        )
        d = ab.to_dict()
        self.assertEqual(d["character_mode"], "work_speech_only")


class TestResearchSteering(unittest.TestCase):
    def test_open_by_default(self):
        rs = ResearchSteering()
        self.assertTrue(rs.is_open)

    def test_user_leaning_with_prompt_not_open(self):
        rs = ResearchSteering(stance="user_leaning", prompt="Favor X")
        self.assertFalse(rs.is_open)

    def test_user_leaning_without_prompt_is_open(self):
        rs = ResearchSteering(stance="user_leaning", prompt=None)
        self.assertTrue(rs.is_open)

    def test_to_dict(self):
        rs = ResearchSteering(stance="open", prompt="hint")
        self.assertEqual(rs.to_dict(), {"stance": "open", "prompt": "hint"})


class TestRoundTurnControl(unittest.TestCase):
    def test_defaults(self):
        rtc = RoundTurnControl()
        d = rtc.to_dict()
        self.assertEqual(d["selection"], "all_roles")
        self.assertEqual(d["speaker_role_ids"], [])
        self.assertEqual(d["non_speaker_mode"], "watch")
        self.assertIsNone(d["moderator_instruction"])
        self.assertEqual(d["skipped_role_ids"], [])

    def test_skipped_role_ids_passed_through(self):
        rtc = RoundTurnControl(selection="selected_roles", speaker_role_ids=["r1"])
        d = rtc.to_dict(skipped_role_ids=["r2", "r3"])
        self.assertEqual(d["skipped_role_ids"], ["r2", "r3"])


class TestModeratorConfig(unittest.TestCase):
    def test_to_dict(self):
        self.assertEqual(ModeratorConfig(enabled=True).to_dict(), {"enabled": True})
        self.assertEqual(ModeratorConfig(enabled=False).to_dict(), {"enabled": False})


class TestPermissionProfile(unittest.TestCase):
    def test_default_safe_profile(self):
        pp = PermissionProfile(id="safe")
        d = pp.to_dict()
        self.assertTrue(d["meeting_read"])
        self.assertFalse(d["filesystem_write"])
        self.assertFalse(d["git_write"])
        self.assertFalse(d["push"])
        self.assertFalse(d["secrets"])
        self.assertFalse(d["implementation"])


class TestRoleDataclass(unittest.TestCase):
    def test_frozen(self):
        r = Role(id="r1", display_name="R1", lens="test", research_focus="focus")
        with self.assertRaises(Exception):
            r.id = "r2"  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()
