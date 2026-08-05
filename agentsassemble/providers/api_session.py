"""Private durable checkpoints and tool-result storage for direct API sessions."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess


class ApiConversationStateError(RuntimeError):
    code = "api_context_checkpoint_invalid"


class ApiContextCheckpointMissing(RuntimeError):
    code = "api_context_checkpoint_missing"


class ApiContextWorkspaceDrift(RuntimeError):
    code = "api_context_workspace_drift"


class ApiContextRecoveryBlocked(RuntimeError):
    code = "api_context_recovery_blocked"


class ApiToolResultStore:
    """Content-addressed private backing for elided API tool results."""

    def __init__(self, state_dir: str | Path) -> None:
        self.state_dir = Path(state_dir).expanduser().resolve()

    @property
    def root(self) -> Path:
        return self.state_dir / "api-context"

    def record(self, content: str) -> str:
        _ensure_private_directory(self.root)
        encoded = content.encode("utf-8")
        digest = hashlib.sha256(encoded).hexdigest()
        path = self.root / "tool-results" / f"{digest}.json"
        if not path.exists():
            _atomic_json_write(
                path,
                {"version": 1, "sha256": digest, "content": content},
            )
        marker = json.dumps(
            {
                "agentsassemble": "delivered_tool_result_elided",
                "ref": f"aa-tool-result://sha256/{digest}",
                "original_bytes": len(encoded),
                "sha256": digest,
            },
            separators=(",", ":"),
        )
        self.validate(marker)
        return marker

    def validate(self, marker: str) -> None:
        try:
            value = json.loads(marker)
            digest = str(value.get("sha256") or "")
            reference = str(value.get("ref") or "")
        except (AttributeError, json.JSONDecodeError, TypeError, ValueError) as error:
            raise ApiConversationStateError(
                "Saved API checkpoint contains an invalid tool-result reference."
            ) from error
        if reference != f"aa-tool-result://sha256/{digest}" or len(digest) != 64:
            raise ApiConversationStateError(
                "Saved API checkpoint contains an invalid tool-result reference."
            )
        path = self.root / "tool-results" / f"{digest}.json"
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
            content = str(stored["content"])
        except (KeyError, OSError, json.JSONDecodeError, TypeError, ValueError) as error:
            raise ApiConversationStateError(
                "Saved API checkpoint refers to a missing tool result."
            ) from error
        if stored.get("version") != 1 or hashlib.sha256(content.encode("utf-8")).hexdigest() != digest:
            raise ApiConversationStateError(
                "Saved API tool result failed its integrity check."
            )


class ApiConversationStore:
    """Own the recoverable state behind one API-backed Agent Session."""

    def __init__(
        self,
        state_dir: str | Path,
        *,
        agent_id: str,
        provider_name: str,
        model: str,
        workspace: str | Path,
        permission_mode: str,
    ) -> None:
        self.state_dir = Path(state_dir).expanduser().resolve()
        self.agent_id = agent_id
        self.provider_name = provider_name
        self.model = model
        self.workspace = Path(workspace).expanduser().resolve()
        self.workspace_write = permission_mode == "workspace_write"
        self.tool_results = ApiToolResultStore(self.state_dir)

    @property
    def root(self) -> Path:
        return self.tool_results.root

    @property
    def path(self) -> Path:
        return self.root / "checkpoints" / f"{self._identity}.json"

    @property
    def recovery_barrier_path(self) -> Path:
        return self.root / "recovery-barriers" / f"{self._identity}.json"

    @property
    def _identity(self) -> str:
        return hashlib.sha256(
            f"{self.agent_id}\0{self.provider_name}\0{self.model}".encode("utf-8")
        ).hexdigest()[:24]

    @property
    def exists(self) -> bool:
        return self.path.is_file()

    def load(
        self,
    ) -> tuple[list[dict[str, object]], set[str], dict[str, str]]:
        self.assert_turn_start_safe()
        if not self.path.exists():
            return [], set(), {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, ValueError) as error:
            raise ApiConversationStateError(
                "Saved API checkpoint is unreadable; refusing to resume without context."
            ) from error
        if (
            not isinstance(payload, dict)
            or payload.get("version") != 3
            or payload.get("agent_id") != self.agent_id
            or payload.get("provider_name") != self.provider_name
            or payload.get("model") != self.model
        ):
            raise ApiConversationStateError(
                "Saved API checkpoint does not match this Agent Session, provider, and model."
            )
        self._validate_workspace_fingerprint(payload.get("workspace_fingerprint"))
        messages = payload.get("messages")
        delivered = payload.get("delivered_tool_call_ids")
        references = payload.get("tool_result_references")
        if (
            not isinstance(messages, list)
            or not isinstance(delivered, list)
            or not isinstance(references, dict)
        ):
            raise ApiConversationStateError("Saved API checkpoint has an invalid shape.")
        validated = _validated_messages(messages)
        tool_ids = {
            str(message.get("tool_call_id") or "")
            for message in validated
            if message.get("role") == "tool"
        }
        delivered_ids = {str(value) for value in delivered if str(value)}
        if not delivered_ids.issubset(tool_ids):
            raise ApiConversationStateError(
                "Saved API checkpoint refers to an unknown tool result."
            )
        markers = {
            str(tool_call_id): str(marker)
            for tool_call_id, marker in references.items()
            if str(tool_call_id) and isinstance(marker, str)
        }
        if not delivered_ids.issubset(markers):
            raise ApiConversationStateError(
                "Saved API checkpoint is missing a delivered tool-result reference."
            )
        for tool_call_id in delivered_ids:
            self._validate_marker(markers[tool_call_id])
            message = next(
                item
                for item in validated
                if item.get("role") == "tool"
                and str(item.get("tool_call_id") or "") == tool_call_id
            )
            if message.get("content") != markers[tool_call_id]:
                raise ApiConversationStateError(
                    "Saved API checkpoint does not use its verified tool-result reference."
                )
        return validated, delivered_ids, markers

    def assert_turn_start_safe(self) -> None:
        if self.workspace_write and self.recovery_barrier_path.exists():
            raise ApiContextRecoveryBlocked(
                "The previous API turn ended while a workspace side effect was pending; "
                "automatic recovery is blocked."
            )

    def begin_side_effect(self, tool_call_id: str, tool_name: str) -> None:
        if not self.workspace_write:
            return
        _atomic_json_write(
            self.recovery_barrier_path,
            {
                "version": 1,
                "agent_id": self.agent_id,
                "provider_name": self.provider_name,
                "model": self.model,
                "workspace": str(self.workspace),
                "tool_call_id": str(tool_call_id),
                "tool_name": str(tool_name),
            },
        )

    def clear_side_effect(self) -> None:
        try:
            self.recovery_barrier_path.unlink()
        except FileNotFoundError:
            return
        _fsync_directory(self.recovery_barrier_path.parent)

    def record_tool_result(self, tool_call_id: str, content: str) -> str:
        del tool_call_id
        return self.tool_results.record(content)

    def persist(
        self,
        messages: list[dict[str, object]],
        delivered_tool_call_ids: set[str],
        tool_result_references: dict[str, str],
    ) -> list[dict[str, object]]:
        _ensure_private_directory(self.root)
        checkpoint_messages = _validated_messages(messages)
        for message in checkpoint_messages:
            if message.get("role") != "tool":
                continue
            tool_call_id = str(message.get("tool_call_id") or "")
            if tool_call_id not in delivered_tool_call_ids:
                continue
            marker = tool_result_references.get(tool_call_id)
            if not marker:
                raise ApiConversationStateError(
                    "Cannot checkpoint a delivered tool result without its private backing file."
                )
            self._validate_marker(marker)
            message["content"] = marker
        payload = {
            "version": 3,
            "agent_id": self.agent_id,
            "provider_name": self.provider_name,
            "model": self.model,
            "workspace_fingerprint": self._workspace_fingerprint(),
            "messages": checkpoint_messages,
            "delivered_tool_call_ids": sorted(delivered_tool_call_ids),
            "tool_result_references": {
                tool_call_id: tool_result_references[tool_call_id]
                for tool_call_id in sorted(delivered_tool_call_ids)
            },
        }
        _atomic_json_write(self.path, payload)
        self.clear_side_effect()
        return checkpoint_messages

    def _validate_marker(self, marker: str) -> None:
        self.tool_results.validate(marker)

    def _workspace_fingerprint(self) -> dict[str, object]:
        if not self.workspace_write:
            return {"kind": "not_required"}
        try:
            return _git_workspace_fingerprint(self.workspace, self.state_dir)
        except (OSError, subprocess.SubprocessError, ValueError):
            return {
                "kind": "unverifiable",
                "workspace": str(self.workspace),
            }

    def _validate_workspace_fingerprint(self, value: object) -> None:
        if not self.workspace_write:
            return
        if not isinstance(value, dict) or value.get("kind") != "git":
            raise ApiContextRecoveryBlocked(
                "This writable API workspace has no verifiable Git checkpoint; "
                "automatic recovery is blocked."
            )
        try:
            current = _git_workspace_fingerprint(self.workspace, self.state_dir)
        except (OSError, subprocess.SubprocessError, ValueError) as error:
            raise ApiContextRecoveryBlocked(
                "The writable API workspace can no longer be verified for recovery."
            ) from error
        if current != value:
            raise ApiContextWorkspaceDrift(
                "The API workspace changed after the last ready checkpoint; "
                "refusing to resume automatically."
            )


def _atomic_json_write(path: Path, payload: dict[str, object]) -> None:
    _ensure_private_directory(path.parent)
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    try:
        temporary.chmod(0o600)
    except OSError:
        pass
    os.replace(temporary, path)
    _fsync_directory(path.parent)


def _fsync_directory(path: Path) -> None:
    try:
        directory_fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _git_workspace_fingerprint(workspace: Path, state_dir: Path) -> dict[str, object]:
    repository_root = Path(
        _git_output(workspace, "rev-parse", "--show-toplevel").decode().strip()
    ).resolve()
    workspace.relative_to(repository_root)
    head = _git_output(repository_root, "rev-parse", "--verify", "HEAD").decode().strip()
    workspace_scope = workspace.relative_to(repository_root).as_posix() or "."
    pathspecs = [workspace_scope]
    try:
        state_dir.relative_to(workspace)
    except ValueError:
        state_scope = ""
    else:
        state_scope = state_dir.relative_to(repository_root).as_posix()
        if state_dir == workspace:
            raise ValueError("API runtime state cannot own the whole workspace.")
    if state_scope:
        pathspecs.append(f":(exclude){state_scope}/**")
    status = _git_output(
        repository_root,
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
        "--",
        *pathspecs,
    )
    difference = _git_output(
        repository_root,
        "diff",
        "--binary",
        "--no-ext-diff",
        "--no-textconv",
        "HEAD",
        "--",
        *pathspecs,
    )
    untracked = _git_output(
        repository_root,
        "ls-files",
        "--others",
        "--exclude-standard",
        "-z",
        "--",
        *pathspecs,
    )
    digest = hashlib.sha256()
    for label, content in ((b"status", status), (b"diff", difference)):
        digest.update(label + b"\0" + content + b"\0")
    for raw_path in sorted(part for part in untracked.split(b"\0") if part):
        relative = raw_path.decode("utf-8", errors="surrogateescape")
        path = repository_root / relative
        digest.update(b"untracked\0" + raw_path + b"\0")
        if path.is_symlink():
            digest.update(os.readlink(path).encode("utf-8", errors="surrogateescape"))
        else:
            digest.update(path.read_bytes())
        digest.update(b"\0")
    return {
        "kind": "git",
        "workspace": str(workspace),
        "repository_root": str(repository_root),
        "head": head,
        "state_sha256": digest.hexdigest(),
    }


def _git_output(cwd: Path, *arguments: str) -> bytes:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
        check=False,
    )
    if completed.returncode != 0:
        raise subprocess.CalledProcessError(
            completed.returncode,
            completed.args,
            output=completed.stdout,
            stderr=completed.stderr,
        )
    return completed.stdout


def _ensure_private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(0o700)
    except OSError:
        pass


def _validated_messages(values: list[object]) -> list[dict[str, object]]:
    messages: list[dict[str, object]] = []
    declared_tool_ids: set[str] = set()
    unresolved_tool_ids: set[str] = set()
    for value in values:
        if not isinstance(value, dict) or value.get("role") not in {
            "system",
            "user",
            "assistant",
            "tool",
        }:
            raise ApiConversationStateError("Saved API checkpoint contains an invalid message.")
        message = dict(value)
        role = message.get("role")
        if role != "tool" and unresolved_tool_ids:
            raise ApiConversationStateError(
                "Saved API checkpoint contains an incomplete assistant/tool transaction."
            )
        if role == "assistant" and isinstance(message.get("tool_calls"), list):
            for call in message["tool_calls"]:
                tool_call_id = str(call.get("id") or "") if isinstance(call, dict) else ""
                if not tool_call_id or tool_call_id in declared_tool_ids:
                    raise ApiConversationStateError(
                        "Saved API checkpoint contains an invalid assistant tool call."
                    )
                declared_tool_ids.add(tool_call_id)
                unresolved_tool_ids.add(tool_call_id)
        if role == "tool":
            tool_call_id = str(message.get("tool_call_id") or "")
            if not tool_call_id or tool_call_id not in unresolved_tool_ids:
                raise ApiConversationStateError(
                    "Saved API checkpoint contains an orphaned tool result."
                )
            unresolved_tool_ids.remove(tool_call_id)
        messages.append(message)
    if unresolved_tool_ids:
        raise ApiConversationStateError(
            "Saved API checkpoint contains an incomplete assistant/tool transaction."
        )
    return messages


__all__ = [
    "ApiContextCheckpointMissing",
    "ApiContextRecoveryBlocked",
    "ApiContextWorkspaceDrift",
    "ApiConversationStateError",
    "ApiConversationStore",
    "ApiToolResultStore",
]
