from __future__ import annotations

import os
from typing import Protocol


class KeyringBackend(Protocol):
    def get_password(self, service_name: str, username: str) -> str | None: ...
    def set_password(self, service_name: str, username: str, password: str) -> None: ...
    def delete_password(self, service_name: str, username: str) -> None: ...


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
        if self._backend is not None:
            try:
                value = self._backend.get_password(self._service_name, clean_provider)
            except Exception:
                value = None
            if value:
                return str(value)
        return str(self._environment.get(_environment_key(clean_provider)) or "").strip()

    def status(self, provider_id: str) -> dict[str, object]:
        clean_provider = _provider_id(provider_id)
        keyring_value = ""
        if self._backend is not None:
            try:
                keyring_value = str(
                    self._backend.get_password(self._service_name, clean_provider) or ""
                )
            except Exception:
                keyring_value = ""
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
        secret = str(value or "").strip()
        if not secret:
            raise ValueError("API key is required.")
        if self._backend is None:
            raise RuntimeError("secure_store_unavailable")
        try:
            self._backend.set_password(self._service_name, clean_provider, secret)
        except Exception as error:
            raise RuntimeError("secure_store_unavailable") from error
        return self.status(clean_provider)

    def delete(self, provider_id: str) -> dict[str, object]:
        clean_provider = _provider_id(provider_id)
        if self._backend is not None:
            try:
                self._backend.delete_password(self._service_name, clean_provider)
            except Exception:
                pass
        return self.status(clean_provider)


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
    except Exception:
        return None


def _provider_id(value: object) -> str:
    clean = str(value or "").strip().lower()
    if clean not in {"cerebras", "deepseek"}:
        raise ValueError(f"Unsupported provider credential: {clean or 'missing'}")
    return clean


def _environment_key(provider_id: str) -> str:
    return f"{provider_id.upper()}_API_KEY"


def secret_provider_id_for_kind(provider_kind: object) -> str:
    return {
        "cerebras_api": "cerebras",
        "deepseek_api": "deepseek",
    }.get(str(provider_kind or "").strip().lower(), "")


PROVIDER_SECRETS = ProviderSecretStore()
