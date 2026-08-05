from __future__ import annotations

from urllib.request import urlopen

from agentsassemble.providers.openai_compatible import (
    OpenAICompatibleApiRuntime,
    UrlOpen,
)
from agentsassemble.providers.room_portal import RoomPortal

LOCAL_OPENAI_PROVIDER_ENDPOINTS = {
    "ollama_api": "http://127.0.0.1:11434/v1",
    "lmstudio_api": "http://127.0.0.1:1234/v1",
}


def local_openai_endpoint(provider_kind: object) -> str:
    return LOCAL_OPENAI_PROVIDER_ENDPOINTS.get(
        str(provider_kind or "").strip().casefold(),
        "",
    )


class LocalOpenAICompatibleRuntime(OpenAICompatibleApiRuntime):
    """Room runtime for a fixed loopback OpenAI-compatible model server."""

    def __init__(
        self,
        agent_id: str,
        *,
        provider_name: str,
        model: str,
        base_url: str,
        message_source: str,
        opener: UrlOpen = urlopen,
        room_portal: RoomPortal | None = None,
        workspace: str = "",
        permission_mode: str = "meeting_read_only",
        state_dir: str = "",
        resume_required: bool = False,
    ) -> None:
        super().__init__(
            agent_id,
            api_key="",
            provider_name=provider_name,
            model=model,
            allowed_models=frozenset({model}),
            reasoning_effort="",
            allowed_reasoning_efforts=frozenset({""}),
            base_url=base_url,
            message_source=message_source,
            require_api_key=False,
            transport="http_sse",
            opener=opener,
            room_portal=room_portal,
            workspace=workspace,
            permission_mode=permission_mode,
            state_dir=state_dir,
            resume_required=resume_required,
        )
