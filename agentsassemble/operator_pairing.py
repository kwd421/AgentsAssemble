"""Compatibility exports for local operator origin pairing."""
from agentsassemble.identity.pairing import (
    OPERATOR_PAIRING_MAX_TTL_SECONDS,
    OPERATOR_PAIRING_TOKEN_PREFIX,
    OperatorPairingService,
    normalize_pairing_origin,
)

__all__ = [
    "OPERATOR_PAIRING_MAX_TTL_SECONDS",
    "OPERATOR_PAIRING_TOKEN_PREFIX",
    "OperatorPairingService",
    "normalize_pairing_origin",
]
