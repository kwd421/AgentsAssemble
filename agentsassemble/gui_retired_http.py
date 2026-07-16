"""Compatibility export for retired HTTP route tombstones."""

from agentsassemble.web.routes.retired import register_retired_legacy_routes


__all__ = ["register_retired_legacy_routes"]
