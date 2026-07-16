"""Compatibility exports for public invite administration routes."""
from agentsassemble.web.routes.public_invite import (
    PublicTunnelControl,
    register_public_invite_admin_routes,
)

__all__ = [
    "PublicTunnelControl",
    "register_public_invite_admin_routes",
]
