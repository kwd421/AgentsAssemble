"""Compatibility exports for agentsassemble.application.room_repository_factory."""

from agentsassemble.application.room_repository_factory import (
    DEFAULT_POSTGRES_DSN_ENV,
    ROOM_REPOSITORY_BACKENDS,
    RoomRepositoryConfigurationError,
    RoomRepositorySettings,
    RoomRepositoryUnavailable,
    build_postgres_application_database,
    build_room_repository,
)

__all__ = [
    'DEFAULT_POSTGRES_DSN_ENV',
    'ROOM_REPOSITORY_BACKENDS',
    'RoomRepositoryConfigurationError',
    'RoomRepositorySettings',
    'RoomRepositoryUnavailable',
    'build_postgres_application_database',
    'build_room_repository',
]
