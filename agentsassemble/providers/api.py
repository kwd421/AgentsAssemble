"""OpenAI-compatible chat adapter for the API-provider lane (master plan 1단계 B).

ONE adapter serves every catalog provider (NVIDIA build / OpenRouter / LM Studio
/ any BYOK OpenAI-compatible endpoint) by swapping base_url + key — the opencode
pattern. The CLI residents (`*_resident.py`) stay as they are; this is the
parallel lane for direct model APIs.

Kept dependency-free (urllib, stdlib) to honor local-first. HTTP is injectable
(`http_post`) so tests never touch the network.

Usage accounting (room consensus): use the provider's `usage` block when given;
otherwise estimate from text length and set `estimated=True`. The caller records
the result via `identity_store.record_usage`.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass

from agentsassemble.providers import catalog

# ~4 chars per token is the rough OpenAI-family heuristic; only used when the
# provider omits a usage block (estimated=True is set so the books stay honest).
_CHARS_PER_TOKEN = 4


@dataclass(frozen=True)
class ApiUsage:
    input_tokens: int
    output_tokens: int
    estimated: bool


@dataclass(frozen=True)
class ApiReply:
    text: str
    usage: ApiUsage
    provider: str
    model: str
    cost_owner: str


class ApiProviderError(RuntimeError):
    """category: auth | rate_limit | unavailable | bad_response | config."""

    def __init__(self, message: str, *, category: str) -> None:
        super().__init__(message)
        self.category = category


def api_error_category(error: Exception) -> str:
    return getattr(error, "category", "") if isinstance(error, ApiProviderError) else ""


def _estimate_tokens(text: str) -> int:
    return max(1, len(str(text or "")) // _CHARS_PER_TOKEN)


def _default_http_post(url: str, body: bytes, headers: dict[str, str], timeout: int) -> tuple[int, bytes]:
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:  # 4xx/5xx — keep status + body for mapping
        return error.code, error.read()
    except urllib.error.URLError as error:  # connection refused, DNS, timeout
        raise ApiProviderError(
            f"Could not reach the provider endpoint: {error.reason}", category="unavailable"
        ) from error


def _raise_for_status(status: int, raw: bytes) -> None:
    if status < 400:
        return
    snippet = raw.decode("utf-8", "replace")[:300]
    if status in (401, 403):
        raise ApiProviderError(f"Authentication failed ({status}): {snippet}", category="auth")
    if status == 429:
        raise ApiProviderError(f"Rate limited ({status}): {snippet}", category="rate_limit")
    if status >= 500:
        raise ApiProviderError(f"Provider error ({status}): {snippet}", category="unavailable")
    raise ApiProviderError(f"Bad request ({status}): {snippet}", category="bad_response")


def _parse_reply(provider_id: str, model_id: str, raw: bytes, *, cost_owner: str) -> ApiReply:
    try:
        data = json.loads(raw.decode("utf-8", "replace"))
    except json.JSONDecodeError as error:
        raise ApiProviderError(f"Provider returned non-JSON: {error}", category="bad_response") from error

    choices = data.get("choices") or []
    if not choices:
        raise ApiProviderError("Provider response had no choices.", category="bad_response")
    message = (choices[0] or {}).get("message") or {}
    text = str(message.get("content") or "").strip()
    if not text:
        raise ApiProviderError("Provider returned an empty message.", category="bad_response")

    usage_block = data.get("usage")
    if isinstance(usage_block, dict) and usage_block.get("prompt_tokens") is not None:
        usage = ApiUsage(
            input_tokens=int(usage_block.get("prompt_tokens") or 0),
            output_tokens=int(usage_block.get("completion_tokens") or 0),
            estimated=False,
        )
    else:
        # provider gave no usage — estimate from text and flag it
        usage = ApiUsage(input_tokens=0, output_tokens=_estimate_tokens(text), estimated=True)

    return ApiReply(text=text, usage=usage, provider=provider_id, model=model_id, cost_owner=cost_owner)


def chat_completion(
    provider_id: str,
    model_id: str,
    messages: list[dict[str, str]],
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    timeout: int = 60,
    temperature: float | None = None,
    key_source: str = "",
    http_post=None,
) -> ApiReply:
    """Call an OpenAI-compatible /chat/completions endpoint.

    Resolves base_url/key from the catalog when not supplied. `http_post` is
    injectable for tests: a callable (url, body, headers, timeout) -> (status, bytes).
    """
    provider = catalog.get_provider(provider_id)
    if not provider:
        raise ApiProviderError(f"Unknown provider: {provider_id!r}", category="config")
    if not catalog.get_model(provider_id, model_id):
        raise ApiProviderError(f"Unknown model: {provider_id}/{model_id}", category="config")

    resolved_base = (base_url or provider.get("base_url") or "").rstrip("/")
    if not resolved_base:
        raise ApiProviderError(f"No base_url for provider {provider_id!r}", category="config")
    resolved_key = api_key if api_key is not None else catalog.resolve_api_key(provider_id)
    needs_key = bool(provider.get("env"))  # local providers (lmstudio) need none
    if needs_key and not resolved_key:
        raise ApiProviderError(
            f"No API key for {provider_id!r} (set ${provider.get('env')}).", category="auth"
        )

    payload: dict[str, object] = {"model": model_id, "messages": messages}
    if temperature is not None:
        payload["temperature"] = temperature
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "AgentsAssemble/1.0",
    }
    if resolved_key:
        headers["Authorization"] = f"Bearer {resolved_key}"

    poster = http_post or _default_http_post
    status, raw = poster(f"{resolved_base}/chat/completions", body, headers, timeout)
    _raise_for_status(status, raw)
    cost_owner = catalog.model_cost_owner(provider_id, model_id, key_source=key_source)
    return _parse_reply(provider_id, model_id, raw, cost_owner=cost_owner)


def chat_completion_with_fallback(
    messages: list[dict[str, str]],
    *,
    primary: tuple[str, str] | None = None,
    timeout: int = 60,
    http_post=None,
) -> ApiReply:
    """Try `primary` (provider, model), then walk the catalog fallback chain on
    rate_limit / unavailable (cli-jaw pattern). auth/config/bad_response fail fast
    — retrying the same broken config on the next provider helps no one."""
    chain: list[tuple[str, str]] = []
    if primary:
        chain.append(primary)
    for pair in catalog.fallback_models():
        if pair not in chain:
            chain.append(pair)

    last_error: Exception | None = None
    for provider_id, model_id in chain:
        try:
            return chat_completion(
                provider_id, model_id, messages, timeout=timeout, http_post=http_post
            )
        except ApiProviderError as error:
            last_error = error
            if error.category in ("rate_limit", "unavailable"):
                continue  # try the next engine
            raise  # auth/config/bad_response — don't paper over with a fallback
    raise last_error or ApiProviderError("No providers available.", category="unavailable")


def record_api_usage(
    store,
    reply: ApiReply,
    *,
    user_id: str = "",
    participant_id: str = "",
    meeting_id: str = "",
) -> None:
    """Write an ApiReply's token usage to the identity store. `store` only needs
    a `record_usage(dict)` method — we don't import IdentityStore (keeps this
    module DB-agnostic). The `estimated` flag rides through so the books stay honest."""
    if store is None:
        return
    store.record_usage(
        {
            "user_id": user_id,
            "participant_id": participant_id,
            "meeting_id": meeting_id,
            "provider": reply.provider,
            "model": reply.model,
            "input_tokens": reply.usage.input_tokens,
            "output_tokens": reply.usage.output_tokens,
            "cost_owner": reply.cost_owner,
            "estimated": reply.usage.estimated,
        }
    )


def run_api_call(
    provider_id: str,
    model_id: str,
    prompt: str,
    *,
    store=None,
    user_id: str = "",
    participant_id: str = "",
    meeting_id: str = "",
    system: str = "",
    key_source: str = "",
    timeout: int = 60,
    http_post=None,
) -> str:
    """End-to-end: prompt string -> model reply text, recording usage as a side
    effect. This is what the `api-call` CLI subcommand and the live-agent API lane
    invoke. The runner's envelope/heartbeat/meta-filter wraps the returned text."""
    messages: list[dict[str, str]] = []
    if system.strip():
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    reply = chat_completion(
        provider_id,
        model_id,
        messages,
        key_source=key_source,
        timeout=timeout,
        http_post=http_post,
    )
    record_api_usage(
        store,
        reply,
        user_id=user_id,
        participant_id=participant_id,
        meeting_id=meeting_id,
    )
    return reply.text
