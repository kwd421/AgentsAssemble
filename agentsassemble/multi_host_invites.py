from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit, urlunsplit

from agentsassemble.legacy.meeting.core.events import clean_lobby_text


LAN_INVITE_SCHEMA = "agentsassemble.lan_invite.v1"
LAN_INVITE_MODE = "lan_invite_token"
LAN_INVITE_TOKEN_PREFIX = "aai1"
NATIVE_REMOTE_ROOM_CLIENT_KIND = "native_remote_room_client"
REMOTE_HTTP_BRIDGE_KIND = "remote_http_bridge"
BASE64URL_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def create_lan_invite_packet(
    *,
    room_url: str,
    meeting_id: str,
    agent_id: str,
    display_name: str,
    provider_kind: str,
    secret: str,
    ttl_seconds: int = 600,
    issued_at: datetime | None = None,
    nonce: str | None = None,
    permission_mode: str = "meeting_read_only",
    public_room_url: str = "",
) -> dict[str, object]:
    clean_secret = _usable_secret(secret)
    if not clean_secret:
        raise ValueError("LAN invite secret is required.")
    clean_provider_kind = clean_lobby_text(provider_kind or "manual", limit=64) or "manual"
    if clean_provider_kind == REMOTE_HTTP_BRIDGE_KIND:
        raise ValueError("LAN invite token mode is for native remote room client admission, not remote_http_bridge.")
    clean_room_url = normalize_lan_room_url(room_url)
    issued = _aware_utc(issued_at or datetime.now(UTC))
    ttl = _positive_ttl_seconds(ttl_seconds)
    claims = {
        "schema": LAN_INVITE_SCHEMA,
        "mode": LAN_INVITE_MODE,
        "client_kind": NATIVE_REMOTE_ROOM_CLIENT_KIND,
        "room_url": clean_room_url,
        "room_host_scope": _room_host_scope(clean_room_url),
        "meeting_id": clean_lobby_text(meeting_id, limit=128),
        "agent": {
            "agent_id": clean_lobby_text(agent_id, limit=64),
            "display_name": clean_lobby_text(display_name or agent_id, limit=128),
            "provider_kind": clean_provider_kind,
        },
        "issued_at": issued.isoformat(),
        "expires_at": (issued + timedelta(seconds=ttl)).isoformat(),
        "nonce": clean_lobby_text(nonce or secrets.token_urlsafe(18), limit=96),
        "admission": _admission_contract(permission_mode=permission_mode),
    }
    # room_url stays loopback (validated LAN scope); clients reached through a
    # public tunnel decode public_room_url instead of failing on 127.0.0.1.
    clean_public_room_url = clean_lobby_text(public_room_url, limit=200)
    if clean_public_room_url:
        claims["public_room_url"] = clean_public_room_url
    _validate_required_claims(claims)
    token = sign_lan_invite_claims(claims, secret=clean_secret)
    return {
        "mode": LAN_INVITE_MODE,
        "client_kind": NATIVE_REMOTE_ROOM_CLIENT_KIND,
        "room_url": clean_room_url,
        "meeting_id": claims["meeting_id"],
        "agent": dict(claims["agent"]),
        "issued_at": claims["issued_at"],
        "expires_at": claims["expires_at"],
        "token": token,
        "admission": _admission_contract(permission_mode=permission_mode),
        "next_step": "A future native remote room client presents this token to the host admission endpoint.",
    }


def sign_lan_invite_claims(claims: dict[str, object], *, secret: str) -> str:
    payload = _canonical_json(claims)
    encoded_payload = _base64url_encode(payload)
    signing_input = f"{LAN_INVITE_TOKEN_PREFIX}.{encoded_payload}".encode("ascii")
    signature = hmac.new(_usable_secret(secret).encode("utf-8"), signing_input, hashlib.sha256).digest()
    return f"{LAN_INVITE_TOKEN_PREFIX}.{encoded_payload}.{_base64url_encode(signature)}"


def verify_lan_invite_token(
    token: str,
    *,
    secret: str,
    expected_meeting_id: str = "",
    expected_agent_id: str = "",
    now: datetime | None = None,
) -> dict[str, object]:
    clean_secret = _usable_secret(secret)
    if not clean_secret:
        raise ValueError("LAN invite secret is required.")
    prefix, encoded_payload, encoded_signature = _split_lan_invite_token(token)
    if prefix != LAN_INVITE_TOKEN_PREFIX or not encoded_payload or not encoded_signature:
        return _failed_invite_verification("malformed_token")
    if not _is_base64url_segment(encoded_payload) or not _is_base64url_segment(encoded_signature):
        return _failed_invite_verification("malformed_token")
    signing_input = f"{prefix}.{encoded_payload}".encode("ascii")
    expected = hmac.new(clean_secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    expected_encoded = _base64url_encode(expected)
    if not hmac.compare_digest(expected_encoded, encoded_signature):
        return _failed_invite_verification("invalid_signature")
    try:
        decoded = json.loads(_base64url_decode(encoded_payload).decode("utf-8"))
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return _failed_invite_verification("malformed_payload")
    if not isinstance(decoded, dict):
        return _failed_invite_verification("malformed_payload")
    if decoded.get("schema") != LAN_INVITE_SCHEMA or decoded.get("mode") != LAN_INVITE_MODE:
        return _failed_invite_verification("unsupported_schema", claims=decoded)
    if decoded.get("client_kind") != NATIVE_REMOTE_ROOM_CLIENT_KIND:
        return _failed_invite_verification("unsupported_client_kind", claims=decoded)
    try:
        normalize_lan_room_url(str(decoded.get("room_url") or ""))
    except ValueError:
        return _failed_invite_verification("invalid_room_url", claims=decoded)
    if not _claims_have_required_identity(decoded):
        return _failed_invite_verification("missing_identity_claims", claims=decoded)
    if _claims_mismatch_expected_identity(
        decoded,
        expected_meeting_id=expected_meeting_id,
        expected_agent_id=expected_agent_id,
    ):
        return _failed_invite_verification("identity_mismatch", claims=decoded)
    expires_at = _parse_datetime(decoded.get("expires_at"))
    if expires_at is None:
        return _failed_invite_verification("malformed_expiry", claims=decoded)
    checked_at = _aware_utc(now or datetime.now(UTC))
    if expires_at <= checked_at:
        return _failed_invite_verification("expired", claims=decoded)
    return {
        "status": "ok",
        "identity_status": "verified",
        "claims": _safe_claims(decoded),
        "admission": _admission_contract(),
    }


def resolve_lan_invite_secret_ref(secret_ref: object) -> str:
    if not isinstance(secret_ref, str):
        return ""
    value = secret_ref.strip()
    if value.startswith("env:"):
        return _usable_secret(os.environ.get(value.removeprefix("env:")) or "")
    if value.startswith("literal:"):
        return _usable_secret(value.removeprefix("literal:"))
    return _usable_secret(value)


def normalize_lan_room_url(room_url: str) -> str:
    value = str(room_url or "").strip().rstrip("/")
    if not value:
        raise ValueError("LAN invite room URL is required.")
    try:
        parsed = urlsplit(value)
    except ValueError:
        raise ValueError("LAN invite room URL must be an HTTP(S) URL.") from None
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("LAN invite room URL must be an HTTP(S) URL.")
    try:
        hostname = parsed.hostname
        parsed.port
    except ValueError:
        raise ValueError("LAN invite room URL must be an HTTP(S) URL with a valid host and port.") from None
    if not hostname:
        raise ValueError("LAN invite room URL must be an HTTP(S) URL with a valid host and port.")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("LAN invite room URL must be HTTP(S) without userinfo, query, or fragment.")
    if not _is_lan_invite_host(hostname):
        raise ValueError("LAN invite room URL must use a connectable LAN, loopback, or private host.")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


def _admission_contract(*, permission_mode: str = "meeting_read_only") -> dict[str, object]:
    return {
        "identity_proof": "hmac_sha256_invite_token",
        "host_verifies": ["token_signature", "token_expiry", "meeting_id", "agent_id"],
        "remote_transport": NATIVE_REMOTE_ROOM_CLIENT_KIND,
        "remote_http_bridge": False,
        "provider_execution": "not_started_by_invite",
        "permission_mode": permission_mode,
    }


def _validate_required_claims(claims: dict[str, object]) -> None:
    agent = claims.get("agent") if isinstance(claims.get("agent"), dict) else {}
    if not claims.get("meeting_id"):
        raise ValueError("LAN invite meeting_id is required.")
    if not agent.get("agent_id"):
        raise ValueError("LAN invite agent_id is required.")


def _claims_have_required_identity(claims: dict[str, object]) -> bool:
    agent = claims.get("agent") if isinstance(claims.get("agent"), dict) else {}
    return bool(str(claims.get("meeting_id") or "").strip() and str(agent.get("agent_id") or "").strip())


def _claims_mismatch_expected_identity(
    claims: dict[str, object],
    *,
    expected_meeting_id: str,
    expected_agent_id: str,
) -> bool:
    expected_meeting = clean_lobby_text(expected_meeting_id, limit=128)
    expected_agent = clean_lobby_text(expected_agent_id, limit=64)
    agent = claims.get("agent") if isinstance(claims.get("agent"), dict) else {}
    if expected_meeting and str(claims.get("meeting_id") or "") != expected_meeting:
        return True
    if expected_agent and str(agent.get("agent_id") or "") != expected_agent:
        return True
    return False


def _split_lan_invite_token(token: str) -> tuple[str, str, str]:
    if not isinstance(token, str):
        return "", "", ""
    parts = token.strip().split(".")
    if len(parts) != 3:
        return "", "", ""
    return parts[0], parts[1], parts[2]


def _is_base64url_segment(value: str) -> bool:
    return bool(isinstance(value, str) and BASE64URL_SEGMENT_RE.fullmatch(value))


def _failed_invite_verification(identity_status: str, *, claims: dict[str, object] | None = None) -> dict[str, object]:
    report: dict[str, object] = {
        "status": "failed",
        "identity_status": identity_status,
        "admission": _admission_contract(),
    }
    if claims is not None and identity_status != "invalid_signature":
        report["claims"] = _safe_claims(claims)
    return report


def _safe_claims(claims: dict[str, object]) -> dict[str, object]:
    safe = dict(claims)
    admission = claims.get("admission") if isinstance(claims.get("admission"), dict) else {}
    # Re-canonicalize the contract but keep the token's actual permission mode
    # (defaulting it silently downgraded participant invites to read-only).
    safe["admission"] = _admission_contract(
        permission_mode=str(admission.get("permission_mode") or "meeting_read_only")
    )
    return safe


def _canonical_json(payload: dict[str, object]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _base64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return _aware_utc(datetime.fromisoformat(value))
    except ValueError:
        return None


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _positive_ttl_seconds(value: int) -> int:
    try:
        ttl = int(value)
    except (TypeError, ValueError):
        raise ValueError("LAN invite ttl_seconds must be positive.") from None
    if ttl <= 0:
        raise ValueError("LAN invite ttl_seconds must be positive.")
    return ttl


def _usable_secret(value: str) -> str:
    cleaned = str(value or "").strip()
    if cleaned in {"", "<redacted>", "literal:<redacted>"}:
        return ""
    return cleaned


def _is_lan_invite_host(hostname: str) -> bool:
    host = str(hostname or "").strip().strip("[]").casefold()
    if not host:
        return False
    if host in {"localhost"} or host.endswith(".local"):
        return True
    if "." not in host and ":" not in host:
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    if (
        address.is_unspecified
        or address.is_multicast
        or address.is_reserved
        or address.is_global
        or _is_broadcast_address(address)
    ):
        return False
    return bool(address.is_loopback or address.is_private or address.is_link_local)


def _is_broadcast_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return isinstance(address, ipaddress.IPv4Address) and int(address) == int(ipaddress.IPv4Address("255.255.255.255"))


def _room_host_scope(room_url: str) -> str:
    parsed = urlsplit(room_url)
    host = (parsed.hostname or "").casefold()
    if host in {"localhost", "127.0.0.1", "::1"}:
        return "loopback"
    if host.endswith(".local"):
        return "lan_hostname"
    return "host_or_lan_ip"
