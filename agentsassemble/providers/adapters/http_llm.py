from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from typing import Any, Callable

from agentsassemble.providers.adapters.base import ProviderAdapter
from agentsassemble.models import ProviderConfig, ResearchDepth, ResearchSteering, Role
from agentsassemble.speech_policy import ROUND_RESPONSE_SCHEMA, ROUND_SPEECH_POLICY


JsonRequester = Callable[[str, dict[str, str], dict[str, Any], int | None], dict[str, Any]]


class HttpLlmAdapter(ProviderAdapter):
    def __init__(
        self,
        provider: ProviderConfig,
        requester: JsonRequester | None = None,
    ) -> None:
        self.provider = provider
        self.name = provider.kind
        self.requester = requester or request_json

    def start_session(self, role: Role, meeting_context: dict[str, Any]) -> dict[str, Any]:
        return {
            "adapter": self.name,
            "role_id": role.id,
            "session_id": None,
            "status": "ready",
            "provider_id": self.provider.id,
            "provider_kind": self.provider.kind,
            "model_id": self.provider.default_model,
            "meeting_id": meeting_context.get("meeting_id"),
        }

    def run_research(
        self,
        role: Role,
        session: dict[str, Any],
        question: str,
        depth: ResearchDepth,
        steering: ResearchSteering,
    ) -> dict[str, Any]:
        text = self._complete(
            system=f"You are {role.display_name} ({role.lens}) in an AgentsAssemble council.",
            prompt=_research_prompt(role, question, depth, steering),
        )
        parsed = parse_json_object(text) or {
            "queries": [],
            "sources": [],
            "summary": text.strip(),
            "confidence": "low",
            "uncertainty": "Provider did not return parseable JSON.",
            "claim_evidence": [],
            "counterclaims": [],
            "rejected_claims": [],
        }
        parsed["role_id"] = role.id
        parsed["display_name"] = role.display_name
        parsed.setdefault("research_steering", steering.to_dict())
        parsed.setdefault("research_depth", _depth_payload(depth))
        parsed.setdefault("coverage_gaps", [])
        parsed.setdefault("counterclaims", [])
        parsed.setdefault("rejected_claims", [])
        parsed["provider"] = self._provider_metadata()
        return parsed

    def run_round(
        self,
        role: Role,
        session: dict[str, Any],
        round_name: str,
        prompt: str,
        public_context: dict[str, Any],
    ) -> dict[str, Any]:
        text = self._complete(
            system=f"You are {role.display_name} ({role.lens}) in an AgentsAssemble council.",
            prompt=_round_prompt(role, round_name, prompt, public_context),
        )
        parsed = parse_json_object(text) or {
            "content": text.strip(),
            "position": "",
            "stance_status": "held",
            "stance_delta": "none",
            "changed_by": [],
            "change_reason": "",
            "remaining_resistance": "",
            "emotion": {},
            "change_conditions": [],
            "confidence": "medium",
        }
        return {
            "role_id": role.id,
            "display_name": role.display_name,
            "round": round_name,
            "content": parsed.get("content", text.strip()),
            "position": parsed.get("position", ""),
            "stance_status": parsed.get("stance_status", "held"),
            "stance_delta": parsed.get("stance_delta", "none"),
            "changed_by": parsed.get("changed_by", []),
            "change_reason": parsed.get("change_reason", ""),
            "remaining_resistance": parsed.get("remaining_resistance", ""),
            "emotion": parsed.get("emotion", {}),
            "change_conditions": parsed.get("change_conditions", []),
            "confidence": parsed.get("confidence", "medium"),
            "provider": self._provider_metadata(),
        }

    def synthesize(
        self,
        session: dict[str, Any],
        question: str,
        public_context: dict[str, Any],
    ) -> dict[str, Any]:
        text = self._complete(
            system="You are the moderator for an AgentsAssemble council.",
            prompt=_synthesis_prompt(question, public_context),
        )
        parsed = parse_json_object(text) or {
            "winner": "",
            "ranking": [],
            "confidence": "low",
            "caveats": ["Provider did not return parseable JSON."],
            "summary": text.strip(),
            "tasks": {},
        }
        parsed["provider"] = self._provider_metadata()
        return parsed

    def _complete(self, system: str, prompt: str) -> str:
        raise NotImplementedError

    def _provider_metadata(self) -> dict[str, Any]:
        return {
            "id": self.provider.id,
            "kind": self.provider.kind,
            "display_name": self.provider.display_name,
            "model": self.provider.default_model,
        }


class OpenAICompatibleChatAdapter(HttpLlmAdapter):
    default_endpoint = "http://127.0.0.1:1234/v1"
    default_model = "local-model"

    def _complete(self, system: str, prompt: str) -> str:
        endpoint = (self.provider.endpoint or self.default_endpoint).rstrip("/")
        headers = {"Content-Type": "application/json"}
        token = resolve_auth_ref(self.provider.auth_ref)
        if token:
            headers["Authorization"] = f"Bearer {token}"
        response = self.requester(
            f"{endpoint}/chat/completions",
            headers,
            {
                "model": self.provider.default_model or self.default_model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                "stream": False,
            },
            self.provider.timeout_seconds,
        )
        return extract_openai_chat_text(response)


class LocalOpenAICompatibleAdapter(OpenAICompatibleChatAdapter):
    pass


class GrokChatAdapter(OpenAICompatibleChatAdapter):
    default_endpoint = "https://api.x.ai/v1"
    default_model = "grok-4"


class AnthropicMessagesAdapter(HttpLlmAdapter):
    default_endpoint = "https://api.anthropic.com/v1/messages"
    default_model = "claude-3-5-sonnet-latest"

    def _complete(self, system: str, prompt: str) -> str:
        token = require_auth_ref(self.provider.auth_ref, self.provider.id)
        response = self.requester(
            self.provider.endpoint or self.default_endpoint,
            {
                "Content-Type": "application/json",
                "x-api-key": token,
                "anthropic-version": "2023-06-01",
            },
            {
                "model": self.provider.default_model or self.default_model,
                "max_tokens": 4096,
                "system": system,
                "messages": [{"role": "user", "content": prompt}],
            },
            self.provider.timeout_seconds,
        )
        return extract_anthropic_text(response)


class GeminiGenerateContentAdapter(HttpLlmAdapter):
    default_model = "gemini-2.5-pro"

    def _complete(self, system: str, prompt: str) -> str:
        model = self.provider.default_model or self.default_model
        key = require_auth_ref(self.provider.auth_ref, self.provider.id)
        endpoint = self.provider.endpoint or f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        separator = "&" if "?" in endpoint else "?"
        response = self.requester(
            f"{endpoint}{separator}key={urllib.parse.quote(key)}",
            {"Content-Type": "application/json"},
            {
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            },
            self.provider.timeout_seconds,
        )
        return extract_gemini_text(response)


def request_json(
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout_seconds: int | None,
) -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    open_kwargs = {} if timeout_seconds is None else {"timeout": timeout_seconds}
    with urllib.request.urlopen(request, **open_kwargs) as response:
        return json.loads(response.read().decode("utf-8"))


def resolve_auth_ref(auth_ref: str | None) -> str | None:
    if not auth_ref:
        return None
    if auth_ref.startswith("env:"):
        return os.environ.get(auth_ref.removeprefix("env:"))
    if auth_ref.startswith("literal:"):
        return auth_ref.removeprefix("literal:")
    return auth_ref


def require_auth_ref(auth_ref: str | None, provider_id: str) -> str:
    token = resolve_auth_ref(auth_ref)
    if not token:
        raise ValueError(f"Provider {provider_id} requires auth_ref with an available API key.")
    return token


def extract_openai_chat_text(response: dict[str, Any]) -> str:
    choices = response.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message", {})
    return str(message.get("content") or "")


def extract_anthropic_text(response: dict[str, Any]) -> str:
    parts = response.get("content") or []
    return "\n".join(str(part.get("text", "")) for part in parts if part.get("type") == "text")


def extract_gemini_text(response: dict[str, Any]) -> str:
    candidates = response.get("candidates") or []
    if not candidates:
        return ""
    parts = candidates[0].get("content", {}).get("parts") or []
    return "\n".join(str(part.get("text", "")) for part in parts if part.get("text"))


def parse_json_object(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.startswith("json"):
            stripped = stripped[4:].strip()
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _depth_payload(depth: ResearchDepth) -> dict[str, Any]:
    return {
        "name": depth.name,
        "label": depth.label,
        "min_sources": depth.min_sources,
        "target_sources": depth.target_sources,
        "min_queries": depth.min_queries,
        "min_claims": depth.min_claims,
        "min_counterclaims": depth.min_counterclaims,
        "notes_per_source": depth.notes_per_source,
    }


def _research_prompt(role: Role, question: str, depth: ResearchDepth, steering: ResearchSteering) -> str:
    return f"""Research question: {question}
Research focus: {role.research_focus}
Personality/style: {json.dumps(role.personality or {}, ensure_ascii=False)}
Source preferences: {json.dumps(role.source_preferences or [], ensure_ascii=False)}
Research depth: {depth.name}
Depth instructions: {depth.instructions}
Research steering: {json.dumps(steering.to_dict(), ensure_ascii=False)}

Act independently. Return Korean user-visible fields. Return only JSON with:
queries, sources, summary, confidence, uncertainty, coverage_gaps, claim_evidence, counterclaims, rejected_claims.
"""


def _round_prompt(role: Role, round_name: str, prompt: str, public_context: dict[str, Any]) -> str:
    return f"""Round: {round_name}
Instruction: {prompt}
Personality/style: {json.dumps(role.personality or {}, ensure_ascii=False)}
Public context:
{json.dumps(public_context, ensure_ascii=False, indent=2)}

{ROUND_SPEECH_POLICY}
Keep your role's distinct stance.
{ROUND_RESPONSE_SCHEMA}
"""


def _synthesis_prompt(question: str, public_context: dict[str, Any]) -> str:
    return f"""Question: {question}
Public council context:
{json.dumps(public_context, ensure_ascii=False, indent=2)}

Return Korean user-visible fields. Return only JSON:
{{"winner":"...","ranking":["..."],"confidence":"low|medium|high","caveats":["..."],"summary":"...","tasks":{{"role_id":"task"}}}}
"""
