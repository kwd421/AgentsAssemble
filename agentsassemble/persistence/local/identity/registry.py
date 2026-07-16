"""Local identity backend construction, caching, and output-root binding."""
from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

from agentsassemble.identity.repository import IdentityBackend
from agentsassemble.persistence.local.identity.migration import (
    migrate_legacy_members_json,
)
from agentsassemble.persistence.local.identity.repository import (
    IDENTITY_DB_FILENAME,
    SqliteIdentityStore,
)

_BACKEND_FACTORIES: dict[str, Callable[..., IdentityBackend]] = {}
_registry_lock = threading.Lock()
_stores: dict[str, IdentityBackend] = {}
_output_root_stores: dict[str, IdentityBackend] = {}
_migrated_member_roots: set[str] = set()


def register_identity_backend(
    kind: str,
    factory: Callable[..., IdentityBackend],
) -> None:
    """Register a backend implementation for explicit construction."""

    _BACKEND_FACTORIES[str(kind).strip().lower()] = factory


def make_identity_backend(kind: str = "sqlite", **config: object) -> IdentityBackend:
    clean_kind = str(kind or "sqlite").strip().lower()
    factory = _BACKEND_FACTORIES.get(clean_kind)
    if factory is None:
        available = ", ".join(sorted(_BACKEND_FACTORIES)) or "(none)"
        raise NotImplementedError(
            f"identity backend {clean_kind!r} is not registered "
            f"(available: {available}). Implement IdentityBackend and call "
            f"register_identity_backend({clean_kind!r}, factory)."
        )
    return factory(**config)


def default_identity_db_path(output_root: Path) -> Path:
    return Path(output_root) / IDENTITY_DB_FILENAME


def identity_store_at(db_path: Path) -> IdentityBackend:
    key = str(Path(db_path).resolve())
    with _registry_lock:
        store = _stores.get(key)
        if store is None:
            store = make_identity_backend("sqlite", db_path=Path(db_path))
            _stores[key] = store
        return store


def register_identity_store_for_output_root(
    output_root: Path,
    store: IdentityBackend,
) -> None:
    """Bind one server data root to its selected identity authority."""

    key = str(Path(output_root).resolve())
    with _registry_lock:
        current = _output_root_stores.get(key)
        if current is not None and current is not store:
            raise RuntimeError(
                f"An identity backend is already registered for output root {key!r}."
            )
        _output_root_stores[key] = store


def unregister_identity_store_for_output_root(
    output_root: Path,
    store: IdentityBackend | None = None,
) -> bool:
    """Release an application-owned root binding without closing the backend."""

    key = str(Path(output_root).resolve())
    with _registry_lock:
        current = _output_root_stores.get(key)
        if current is None or (store is not None and current is not store):
            return False
        del _output_root_stores[key]
        return True


def identity_store_for_output_root(output_root: Path) -> IdentityBackend:
    """Return the configured backend or the cached local SQLite fallback.

    Legacy room membership import runs at most once per output root and only
    while the selected local store is empty.
    """

    key = str(Path(output_root).resolve())
    with _registry_lock:
        configured = _output_root_stores.get(key)
    if configured is not None:
        return configured

    store = identity_store_at(default_identity_db_path(output_root))
    if key not in _migrated_member_roots:
        with _registry_lock:
            if key not in _migrated_member_roots:
                if store.count_memberships() == 0:
                    migrate_legacy_members_json(
                        store,
                        Path(output_root) / "room_members.json",
                    )
                _migrated_member_roots.add(key)
    return store


def reset_identity_store_registry() -> None:
    """Testing only: drop cached store instances without deleting files."""

    with _registry_lock:
        _stores.clear()
        _output_root_stores.clear()
        _migrated_member_roots.clear()


register_identity_backend(
    "sqlite",
    lambda db_path: SqliteIdentityStore(Path(db_path)),
)


__all__ = [
    "default_identity_db_path",
    "identity_store_at",
    "identity_store_for_output_root",
    "make_identity_backend",
    "register_identity_backend",
    "register_identity_store_for_output_root",
    "reset_identity_store_registry",
    "unregister_identity_store_for_output_root",
]
