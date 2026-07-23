"""Sanitized DeepSeek account balance from the provider's public API."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from agentsassemble.providers.usage_contract import ProviderUsageUnavailable
from agentsassemble.providers.secrets import PROVIDER_SECRETS


DEEPSEEK_BALANCE_URL = "https://api.deepseek.com/user/balance"
BalanceFetcher = Callable[[str], dict[str, object]]
CredentialReader = Callable[[], str]


class DeepSeekUsageService:
    def __init__(
        self,
        *,
        credential_reader: CredentialReader | None = None,
        fetcher: BalanceFetcher | None = None,
        cache_seconds: float = 60.0,
    ) -> None:
        self._credential_reader = (
            credential_reader
            or (lambda: PROVIDER_SECRETS.get("deepseek"))
        )
        self._fetcher = fetcher or fetch_deepseek_balance
        self._cache_seconds = max(1.0, float(cache_seconds))
        self._lock = threading.Lock()
        self._cached_at = 0.0
        self._cached: dict[str, object] = {}

    def read(
        self,
        *,
        model: str = "",
        refresh: bool = False,
    ) -> dict[str, object]:
        del model
        now = time.monotonic()
        with self._lock:
            if not refresh and self._cached and now - self._cached_at < self._cache_seconds:
                return dict(self._cached)
            api_key = self._credential_reader()
            if not api_key:
                raise ProviderUsageUnavailable("deepseek_credential_missing")
            payload = _public_deepseek_balance(self._fetcher(api_key))
            self._cached = payload
            self._cached_at = time.monotonic()
            return dict(payload)


def fetch_deepseek_balance(api_key: str) -> dict[str, object]:
    request = Request(
        DEEPSEEK_BALANCE_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        },
    )
    try:
        with urlopen(request, timeout=10.0) as response:
            payload = json.loads(response.read())
    except HTTPError as error:
        if error.code in {401, 403}:
            raise ProviderUsageUnavailable("deepseek_authentication_required") from error
        raise ProviderUsageUnavailable(f"deepseek_balance_http_{error.code}") from error
    except (OSError, URLError, json.JSONDecodeError) as error:
        raise ProviderUsageUnavailable("deepseek_balance_unavailable") from error
    if not isinstance(payload, dict):
        raise ProviderUsageUnavailable("deepseek_balance_invalid_response")
    return payload


def _public_deepseek_balance(payload: dict[str, object]) -> dict[str, object]:
    source_balances = payload.get("balance_infos")
    if not isinstance(source_balances, list):
        raise ProviderUsageUnavailable("deepseek_balance_invalid_response")
    balances: list[dict[str, str]] = []
    for value in source_balances:
        if not isinstance(value, dict):
            continue
        currency = str(value.get("currency") or "").strip().upper()
        amount = _decimal_text(value.get("total_balance"))
        if currency and amount:
            balances.append({"currency": currency[:12], "amount": amount})
    if not balances:
        raise ProviderUsageUnavailable("deepseek_balance_invalid_response")
    return {
        "provider_id": "deepseek",
        "status": "ready",
        "source": "deepseek_account_balance",
        "observed_at": datetime.now(UTC).isoformat(),
        "quota_state": "unknown",
        "quota_windows": [],
        "account_available": bool(payload.get("is_available")),
        "account_balances": balances[:4],
    }


def _decimal_text(value: object) -> str:
    try:
        amount = Decimal(str(value))
        if not amount.is_finite():
            return ""
        return format(amount.quantize(Decimal("0.0001")).normalize(), "f")
    except (InvalidOperation, ValueError):
        return ""


DEEPSEEK_USAGE = DeepSeekUsageService()


__all__ = [
    "DEEPSEEK_BALANCE_URL",
    "DEEPSEEK_USAGE",
    "DeepSeekUsageService",
    "fetch_deepseek_balance",
]
