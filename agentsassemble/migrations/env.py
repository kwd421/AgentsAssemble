from __future__ import annotations

from alembic import context


def run_migrations_online() -> None:
    connection = context.config.attributes.get("connection")
    if connection is None:
        raise RuntimeError(
            "AgentsAssemble migrations require a programmatic connection; raw database URLs are not read from alembic.ini."
        )
    context.configure(connection=connection, target_metadata=None, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    raise RuntimeError("Offline room schema migration is not supported.")
run_migrations_online()
