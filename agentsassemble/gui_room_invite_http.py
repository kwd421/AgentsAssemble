"""Compatibility export for room invite and admission routes."""
from agentsassemble.web.routes.room_invite import register_invite_admission_routes

__all__ = ["register_invite_admission_routes"]
