from __future__ import annotations

from urllib.request import urlopen

from agentsassemble.providers.openai_compatible import (
    OpenAICompatibleApiRuntime,
    UrlOpen,
)
from agentsassemble.providers.room_portal import RoomPortal


class CerebrasApiRuntime(OpenAICompatibleApiRuntime):
    """Cerebras GPT-OSS configuration on the shared API room runtime."""

    def __init__(
        self,
        agent_id: str,
        *,
        api_key: str,
        model: str = "gpt-oss-120b",
        reasoning_effort: str = "low",
        base_url: str = "https://api.cerebras.ai/v1",
        opener: UrlOpen = urlopen,
        room_portal: RoomPortal | None = None,
    ) -> None:
        super().__init__(
            agent_id,
            api_key=api_key,
            provider_name="Cerebras",
            model=model,
            allowed_models=frozenset({"gpt-oss-120b"}),
            reasoning_effort=reasoning_effort,
            allowed_reasoning_efforts=frozenset({"low", "medium", "high"}),
            base_url=base_url,
            message_source="cerebras_sse",
            request_headers={"X-Cerebras-Version-Patch": "2"},
            opener=opener,
            room_portal=room_portal,
        )
