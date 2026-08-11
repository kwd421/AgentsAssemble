from __future__ import annotations

from agentsassemble.providers.openai_compatible import (
    OpenAICompatibleApiRuntime,
    UrlOpen,
)
from agentsassemble.providers.remote_http import safe_remote_urlopen
from agentsassemble.providers.room_portal import RoomPortal


class DeepSeekApiRuntime(OpenAICompatibleApiRuntime):
    """DeepSeek's model and thinking controls on the shared API room runtime."""

    def __init__(
        self,
        agent_id: str,
        *,
        api_key: str,
        model: str = "deepseek-v4-flash",
        reasoning_effort: str = "high",
        thinking: bool = True,
        base_url: str = "https://api.deepseek.com",
        opener: UrlOpen = safe_remote_urlopen,
        room_portal: RoomPortal | None = None,
    ) -> None:
        super().__init__(
            agent_id,
            api_key=api_key,
            provider_name="DeepSeek",
            model=model,
            allowed_models=frozenset({"deepseek-v4-flash", "deepseek-v4-pro"}),
            reasoning_effort=reasoning_effort,
            allowed_reasoning_efforts=frozenset({"high", "max"}),
            base_url=base_url,
            message_source="deepseek_sse",
            variant="thinking" if thinking else "non_thinking",
            include_reasoning_in_messages=True,
            # DeepSeek V4 thinking mode supports tools but rejects the
            # OpenAI-format tool_choice field. The runtime still verifies that
            # the first observation round actually read the room.
            supports_tool_choice=not thinking,
            request_payload={
                "thinking": {"type": "enabled" if thinking else "disabled"},
            },
            opener=opener,
            room_portal=room_portal,
        )
