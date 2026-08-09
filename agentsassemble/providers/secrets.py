from __future__ import annotations

import os
from typing import Protocol

from agentsassemble.diagnostics.sensitive_text import (
    MAX_EXACT_SENSITIVE_VALUE_LENGTH,
    validate_redactable_sensitive_value,
)
from agentsassemble.providers.remote_openai import (
    remote_openai_credential_ids,
    remote_openai_profile,
)


MAX_PROVIDER_SECRET_LENGTH = MAX_EXACT_SENSITIVE_VALUE_LENGTH


class KeyringBackend(Protocol):
    def get_password(self, service_name: str, username: str) -> str | None: ...
    def set_password(self, service_name: str, username: str, password: str) -> None: ...
    def delete_password(self, service_name: str, username: str) -> None: ...


class ProviderSecretStoreUnavailable(RuntimeError):
    """Raised when the configured secure-storage backend cannot be trusted."""


class _UnavailableKeyringBackend:
    def __init__(self, error: Exception) -> None:
        self._error = error

    def _raise(self) -> None:
        raise self._error

    def get_password(self, service_name: str, username: str) -> str | None:
        del service_name, username
        self._raise()

    def set_password(self, service_name: str, username: str, password: str) -> None:
        del service_name, username, password
        self._raise()

    def delete_password(self, service_name: str, username: str) -> None:
        del service_name, username
        self._raise()


class ProviderSecretStore:
    """Server-owned provider secrets; values never cross public diagnostics."""

    def __init__(
        self,
        *,
        backend: KeyringBackend | None = None,
        environment: dict[str, str] | None = None,
        service_name: str = "AgentsAssemble",
    ) -> None:
        self._backend = backend if backend is not None else _load_keyring_backend()
        self._environment = environment if environment is not None else os.environ
        self._service_name = service_name

    def get(self, provider_id: str) -> str:
        clean_provider = _provider_id(provider_id)
        value = self._keyring_value(clean_provider)
        if value:
            return value
        return str(self._environment.get(_environment_key(clean_provider)) or "").strip()

    def status(self, provider_id: str) -> dict[str, object]:
        clean_provider = _provider_id(provider_id)
        keyring_value = self._keyring_value(clean_provider)
        environment_configured = bool(
            str(self._environment.get(_environment_key(clean_provider)) or "").strip()
        )
        source = "keyring" if keyring_value else "environment" if environment_configured else "missing"
        return {
            "configured": source != "missing",
            "source": source,
        }

    def set(self, provider_id: str, value: str) -> dict[str, object]:
        clean_provider = _provider_id(provider_id)
        secret = validate_provider_secret(value)
        if self._backend is None:
            raise ProviderSecretStoreUnavailable("secure_store_unavailable")
        try:
            self._backend.set_password(self._service_name, clean_provider, secret)
        except Exception as error:
            raise ProviderSecretStoreUnavailable("secure_store_unavailable") from error
        return self.status(clean_provider)

    def delete(self, provider_id: str) -> dict[str, object]:
        clean_provider = _provider_id(provider_id)
        if self._backend is not None:
            try:
                self._backend.delete_password(self._service_name, clean_provider)
            except Exception as error:
                raise ProviderSecretStoreUnavailable("secure_store_unavailable") from error
        return self.status(clean_provider)

    def _keyring_value(self, provider_id: str) -> str:
        if self._backend is None:
            return ""
        try:
            return str(
                self._backend.get_password(self._service_name, provider_id) or ""
            )
        except Exception as error:
            raise ProviderSecretStoreUnavailable("secure_store_unavailable") from error


def _load_keyring_backend() -> KeyringBackend | None:
    try:
        import keyring
        from keyring.backend import KeyringBackend as BaseKeyringBackend
    except ImportError:
        return None
    try:
        backend = keyring.get_keyring()
        if not isinstance(backend, BaseKeyringBackend) or float(getattr(backend, "priority", 0)) <= 0:
            return None
        return keyring
    except Exception as error:
        return _UnavailableKeyringBackend(error)


def _provider_id(value: object) -> str:
    clean = str(value or "").strip().lower()
    if clean not in remote_openai_credential_ids():
        raise ValueError(f"Unsupported provider credential: {clean or 'missing'}")
    return clean


def _environment_key(provider_id: str) -> str:
    profile = remote_openai_profile(provider_id)
    return profile.credential_env if profile is not None else ""


def secret_provider_id_for_kind(provider_kind: object) -> str:
    profile = remote_openai_profile(provider_kind)
    return profile.provider_id if profile is not None else ""


def validate_provider_secret(value: object) -> str:
    return validate_redactable_sensitive_value(
        str(value or "").strip(),
        label="API key",
        maximum_length=MAX_PROVIDER_SECRET_LENGTH,
    )


PROVIDER_SECRETS = ProviderSecretStore()
