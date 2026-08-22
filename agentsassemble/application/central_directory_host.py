from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import stat
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

CENTRAL_URL_ENV = "AGENTSASSEMBLE_CENTRAL_URL"
CENTRAL_HEARTBEAT_SECONDS_ENV = "AGENTSASSEMBLE_CENTRAL_HEARTBEAT_SECONDS"
CENTRAL_LEASE_SECONDS_ENV = "AGENTSASSEMBLE_CENTRAL_LEASE_SECONDS"
CENTRAL_HOST_LABEL_ENV = "AGENTSASSEMBLE_CENTRAL_HOST_LABEL"

_HOST_IDENTITY_FILE_LOCK = threading.RLock()


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _ensure_private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass


def _atomic_write(path: Path, payload: bytes, *, mode: int = 0o600) -> None:
    _ensure_private_directory(path.parent)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(6)}.tmp")
    try:
        with temporary.open("xb") as stream:
            os.chmod(temporary, mode)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        try:
            os.chmod(path, mode)
        except OSError:
            pass
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _write_once(path: Path, payload: bytes, *, mode: int = 0o600) -> bool:
    """Create a secret file without ever replacing an existing identity."""

    _ensure_private_directory(path.parent)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    try:
        descriptor = os.open(path, flags, mode)
    except FileExistsError:
        return False
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.chmod(path, mode)
        except OSError:
            pass
        return True
    except BaseException:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        raise


def normalize_central_url(value: str) -> str:
    clean = str(value or "").strip().rstrip("/")
    if not clean:
        return ""
    try:
        parsed = urlsplit(clean)
        port = parsed.port
    except ValueError:
        raise ValueError("central directory URL is invalid") from None
    hostname = (parsed.hostname or "").lower().strip("[]")
    loopback = hostname in {"localhost", "127.0.0.1", "::1"}
    if (
        not hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
        or (parsed.scheme != "https" and not (parsed.scheme == "http" and loopback))
    ):
        raise ValueError(
            "central directory URL must be HTTPS, or loopback HTTP for development"
        )
    netloc = parsed.hostname or ""
    if ":" in netloc and not netloc.startswith("["):
        netloc = f"[{netloc}]"
    if port is not None:
        netloc = f"{netloc}:{port}"
    return urlunsplit((parsed.scheme, netloc, "", "", ""))


class PublicUrlRuntime(Protocol):
    def public_url(self) -> str: ...


class HostIdentity:
    """Persistent Ed25519 identity for one local AgentsAssemble engine."""

    def __init__(self, *, output_root: Path, server_id: str) -> None:
        clean_server_id = str(server_id or "").strip()
        if not clean_server_id:
            raise ValueError("server_id is required")
        self.server_id = clean_server_id
        self._directory = Path(output_root) / "central-directory"
        self._key_path = self._directory / "host-ed25519.pem"
        self._generation_path = self._directory / "endpoint-generation.json"
        self._lock = threading.RLock()
        self._private_key = self._load_or_create_private_key()

    @staticmethod
    def _parse_private_key(payload: bytes) -> Ed25519PrivateKey:
        key = serialization.load_pem_private_key(payload, password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise ValueError("central directory host key is not Ed25519")
        return key

    def _load_or_create_private_key(self) -> Ed25519PrivateKey:
        with _HOST_IDENTITY_FILE_LOCK:
            try:
                payload = self._key_path.read_bytes()
            except FileNotFoundError:
                generated = Ed25519PrivateKey.generate()
                payload = generated.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                )
                if _write_once(self._key_path, payload):
                    return generated
                payload = self._key_path.read_bytes()
            try:
                os.chmod(self._key_path, 0o600)
            except OSError:
                pass
            return self._parse_private_key(payload)

    def public_jwk(self) -> dict[str, object]:
        public_bytes = self._private_key.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
        return {
            "crv": "Ed25519",
            "ext": True,
            "key_ops": ["verify"],
            "kty": "OKP",
            "x": _base64url(public_bytes),
        }

    def fingerprint(self) -> str:
        return _base64url(hashlib.sha256(_canonical_json(self.public_jwk()).encode()).digest())

    def server_info(
        self,
        *,
        central_status: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        return {
            "server_id": self.server_id,
            "host_public_key_jwk": self.public_jwk(),
            "host_key_fingerprint": self.fingerprint(),
            "protocol_version": 1,
            "status": "ready",
            "central_directory": dict(central_status or {"enabled": False}),
        }

    def next_generation(self) -> int:
        with _HOST_IDENTITY_FILE_LOCK, self._lock:
            current = 0
            try:
                stored = json.loads(self._generation_path.read_text(encoding="utf-8"))
                current = int(stored.get("generation") or 0)
            except (FileNotFoundError, OSError, ValueError, TypeError, json.JSONDecodeError):
                current = 0
            generation = max(current + 1, int(time.time_ns() // 1_000_000))
            _atomic_write(
                self._generation_path,
                json.dumps({"generation": generation}, separators=(",", ":")).encode(),
            )
            return generation

    def sign(self, payload: bytes) -> str:
        return _base64url(self._private_key.sign(payload))

    def key_file_mode(self) -> int:
        return stat.S_IMODE(self._key_path.stat().st_mode)


class CentralDirectoryHost:
    """Publishes the current public origin with a host-signed expiring lease."""

    def __init__(
        self,
        *,
        identity: HostIdentity,
        public_url_runtime: PublicUrlRuntime,
        central_url: str,
        label: str = "",
        heartbeat_seconds: float = 300.0,
        lease_seconds: int = 600,
        request_open: Callable[..., object] = urlopen,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.identity = identity
        self._runtime = public_url_runtime
        self._central_url = normalize_central_url(central_url)
        self._label = str(label or "").strip()[:80]
        self._heartbeat_seconds = max(30.0, float(heartbeat_seconds))
        self._lease_seconds = max(60, min(900, int(lease_seconds)))
        self._request_open = request_open
        self._clock = clock
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._registered_origin = ""
        self._last_attempt_at = 0.0
        self._last_success_at = 0.0
        self._last_error = ""
        self._failure_count = 0
        self._next_attempt_at = 0.0

    @classmethod
    def from_environment(
        cls,
        *,
        output_root: Path,
        server_id: str,
        public_url_runtime: PublicUrlRuntime,
        environ: Mapping[str, str] | None = None,
    ) -> CentralDirectoryHost | None:
        source = environ if environ is not None else os.environ
        central_url = str(source.get(CENTRAL_URL_ENV) or "").strip()
        if not central_url:
            return None
        identity = HostIdentity(output_root=output_root, server_id=server_id)
        return cls(
            identity=identity,
            public_url_runtime=public_url_runtime,
            central_url=central_url,
            label=str(source.get(CENTRAL_HOST_LABEL_ENV) or ""),
            heartbeat_seconds=float(source.get(CENTRAL_HEARTBEAT_SECONDS_ENV) or 300),
            lease_seconds=int(source.get(CENTRAL_LEASE_SECONDS_ENV) or 600),
        )

    def start(self) -> None:
        if not self._central_url:
            return
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="agentsassemble-central-directory-host",
                daemon=True,
            )
            self._thread.start()

    def wake(self) -> None:
        """Retry promptly after a local ownership or tunnel-state transition."""

        with self._lock:
            self._failure_count = 0
            self._next_attempt_at = 0.0
        self._wake_event.set()

    def close(self) -> None:
        self._stop_event.set()
        self._wake_event.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=3.0)
        with self._lock:
            should_clear = bool(self._registered_origin)
        if should_clear:
            self._publish_offline()

    def status(self) -> dict[str, object]:
        with self._lock:
            return {
                "enabled": bool(self._central_url),
                "registered_origin": self._registered_origin,
                "last_attempt_at": self._last_attempt_at,
                "last_success_at": self._last_success_at,
                "last_error": self._last_error,
                "next_attempt_at": self._next_attempt_at,
            }

    def server_info(self) -> dict[str, object]:
        return self.identity.server_info(central_status=self.status())

    def register_payload(self) -> dict[str, object]:
        return {
            "server_id": self.identity.server_id,
            "label": self._label,
            "host_public_key_jwk": self.identity.public_jwk(),
            "host_key_fingerprint": self.identity.fingerprint(),
        }

    def sync_once(self) -> None:
        origin = str(self._runtime.public_url() or "").strip().rstrip("/")
        with self._lock:
            registered = self._registered_origin
            last_success_at = self._last_success_at
        now = self._clock()
        with self._lock:
            if now < self._next_attempt_at:
                return
        if origin:
            if origin != registered or now - last_success_at >= self._heartbeat_seconds:
                self._publish_online(origin)
        elif registered:
            self._publish_offline()

    def _run(self) -> None:
        while not self._stop_event.is_set():
            self.sync_once()
            self._wake_event.wait(timeout=1.0)
            self._wake_event.clear()

    def _publish_online(self, origin: str) -> None:
        now = int(self._clock())
        body = {
            "generation": self.identity.next_generation(),
            "issued_at": now,
            "lease_expires_at": now + self._lease_seconds,
            "origin": origin,
        }
        if self._send("PUT", body):
            with self._lock:
                self._registered_origin = origin

    def _publish_offline(self) -> None:
        now = int(self._clock())
        body = {
            "generation": self.identity.next_generation(),
            "issued_at": now,
        }
        try:
            self._send("DELETE", body)
        finally:
            with self._lock:
                self._registered_origin = ""

    def _send(self, method: str, body: dict[str, object]) -> bool:
        server_path = f"/v1/servers/{quote(self.identity.server_id, safe='')}/endpoint"
        body_bytes = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
        timestamp = int(self._clock())
        nonce = _base64url(secrets.token_bytes(18))
        body_hash = _base64url(hashlib.sha256(body_bytes).digest())
        canonical = "\n".join(
            [
                "AA-HOST-1",
                method,
                server_path,
                str(timestamp),
                nonce,
                body_hash,
            ]
        ).encode()
        request = Request(
            f"{self._central_url}{server_path}",
            data=body_bytes,
            method=method,
            headers={
                "Content-Type": "application/json",
                "X-AA-Host-Timestamp": str(timestamp),
                "X-AA-Host-Nonce": nonce,
                "X-AA-Host-Signature": self.identity.sign(canonical),
                "User-Agent": "AgentsAssemble/central-directory-host-v1",
            },
        )
        with self._lock:
            self._last_attempt_at = self._clock()
        try:
            response = self._request_open(request, timeout=5.0)
            try:
                status = int(getattr(response, "status", 200))
                if not 200 <= status < 300:
                    raise RuntimeError(f"central directory returned HTTP {status}")
                read = getattr(response, "read", None)
                if callable(read):
                    read(64 * 1024)
            finally:
                close = getattr(response, "close", None)
                if callable(close):
                    close()
        except HTTPError as error:
            self._record_failure(f"HTTP {error.code}")
            return False
        except (URLError, OSError, TimeoutError, RuntimeError) as error:
            self._record_failure(type(error).__name__)
            return False
        with self._lock:
            self._last_success_at = self._clock()
            self._last_error = ""
            self._failure_count = 0
            self._next_attempt_at = 0.0
        return True

    def _record_failure(self, message: str) -> None:
        with self._lock:
            self._last_error = str(message or "central directory request failed")[:160]
            self._failure_count += 1
            retry_seconds = min(300.0, float(2 ** min(self._failure_count, 8)))
            self._next_attempt_at = self._clock() + retry_seconds
