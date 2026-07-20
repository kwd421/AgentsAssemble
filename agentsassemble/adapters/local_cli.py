"""Compatibility exports for the one-shot legacy CLI adapter."""

from agentsassemble.providers.adapters.local_cli import LocalCliAdapter, LocalCliError

__all__ = ["LocalCliAdapter", "LocalCliError"]
