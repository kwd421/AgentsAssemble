"""Turn notification projection helpers for Grok ACP."""

from __future__ import annotations

import queue
from collections.abc import Callable

from agentsassemble.room.text import clean_room_text


def tool_category(title: str) -> str:
    value = str(title or "").casefold()
    if any(word in value for word in ("read", "file", "open")):
        return "file_read"
    if any(word in value for word in ("search", "find", "grep")):
        return "search"
    if any(word in value for word in ("web", "http", "fetch", "browser")):
        return "web"
    if any(word in value for word in ("shell", "command", "exec", "terminal")):
        return "command"
    return "tool"


def grok_tool_activity(update: dict[str, object]) -> tuple[str, str, str]:
    metadata = update.get("_meta") if isinstance(update.get("_meta"), dict) else {}
    tool_metadata = (
        metadata.get("x.ai/tool")
        if isinstance(metadata.get("x.ai/tool"), dict)
        else {}
    )
    raw_input = update.get("rawInput") if isinstance(update.get("rawInput"), dict) else {}
    name = clean_room_text(
        tool_metadata.get("name")
        or update.get("name")
        or update.get("title"),
        limit=120,
    )
    label = clean_room_text(tool_metadata.get("label") or name, limit=120)
    title = clean_room_text(update.get("title"), limit=600)
    category_source = " ".join(part for part in (name, label, title) if part)

    detail_value = ""
    for key in (
        "command",
        "target_file",
        "file_path",
        "path",
        "pattern",
        "query",
        "url",
        "target_directory",
        "description",
    ):
        candidate = raw_input.get(key)
        if isinstance(candidate, str) and candidate.strip():
            detail_value = candidate
            break
    activity_title = label or name or title or "Tool"
    detail = detail_value or title
    return (
        category_source,
        clean_room_text(activity_title, limit=120),
        clean_room_text(detail, limit=600),
    )


class GrokAcpTurnProjectionMixin:
    def _consume_notifications(
        self,
        session_id: str,
        content_parts: list[str],
        *,
        on_delta: Callable[[str], None] | None,
        on_activity: Callable[[dict[str, object]], None] | None,
    ) -> None:
        while True:
            try:
                message = self._notifications.get_nowait()
            except queue.Empty:
                return
            if message.get("_eof"):
                raise RuntimeError(
                    "Grok ACP runtime exited before turn completion."
                )
            method = str(message.get("method") or "")
            params = (
                message.get("params")
                if isinstance(message.get("params"), dict)
                else {}
            )
            if method == "_x.ai/sessions/changed":
                for session in list(params.get("upserted") or []):
                    if (
                        isinstance(session, dict)
                        and session.get("sessionId") == session_id
                    ):
                        yolo = session.get("yolo")
                        if isinstance(yolo, bool):
                            self._yolo_mode = yolo
                            if yolo:
                                raise RuntimeError(
                                    "Grok ACP safety isolation failed: "
                                    "always-approve mode is active."
                                )
                continue
            if method != "session/update" or params.get("sessionId") != session_id:
                continue
            update = (
                params.get("update")
                if isinstance(params.get("update"), dict)
                else {}
            )
            update_type = str(update.get("sessionUpdate") or "")
            if update_type == "agent_thought_chunk":
                content = (
                    update.get("content")
                    if isinstance(update.get("content"), dict)
                    else {}
                )
                self._emit_thought_activity(
                    content.get("text"),
                    on_activity=on_activity,
                )
                continue
            if update_type in {"tool_call", "tool_call_update"}:
                self._emit_thought_activity(
                    "",
                    on_activity=on_activity,
                    force=True,
                )
                if on_activity is not None:
                    raw_status = str(update.get("status") or "running").casefold()
                    status = (
                        "completed"
                        if raw_status
                        in {
                            "cancelled",
                            "completed",
                            "error",
                            "failed",
                            "success",
                            "done",
                        }
                        else "running"
                    )
                    category_source, activity_title, detail = grok_tool_activity(update)
                    tool_call_id = clean_room_text(
                        update.get("toolCallId") or update.get("tool_call_id"),
                        limit=128,
                    )
                    if self._should_emit_tool_activity(
                        tool_call_id,
                        status=status,
                        detail=detail,
                    ):
                        on_activity(
                            {
                                "category": tool_category(category_source),
                                "status": status,
                                "activity_id": tool_call_id,
                                "activity_title": activity_title,
                                "activity_detail": detail,
                                "content": (
                                    f"{activity_title}: {detail}"
                                    if detail
                                    else activity_title
                                ),
                            }
                        )
                continue
            if update_type != "agent_message_chunk":
                continue
            self._emit_thought_activity(
                "",
                on_activity=on_activity,
                force=True,
            )
            content = (
                update.get("content")
                if isinstance(update.get("content"), dict)
                else {}
            )
            delta = str(content.get("text") or "")
            if not delta:
                continue
            content_parts.append(delta)
            if on_delta is not None:
                on_delta(delta)

    def _emit_thought_activity(
        self,
        value: object,
        *,
        on_activity: Callable[[dict[str, object]], None] | None,
        force: bool = False,
    ) -> None:
        if on_activity is None:
            return
        raw = str(value or "")
        with self._lock:
            if raw:
                self._active_thought_text = (self._active_thought_text + raw)[
                    :2000
                ]
            thought = clean_room_text(self._active_thought_text, limit=2000)
            previous = self._last_emitted_thought_text
            should_emit = bool(
                thought
                and thought != previous
                and (
                    force
                    or not previous
                    or len(thought) - len(previous) >= 40
                    or any(marker in raw for marker in (".", "!", "?", "\n"))
                )
            )
            if should_emit:
                self._last_emitted_thought_text = thought
        if should_emit:
            on_activity(
                {
                    "category": "reasoning",
                    "status": "running",
                    "activity_id": "reasoning",
                    "activity_title": "생각",
                    "activity_detail": thought,
                    "content": thought,
                }
            )

    def _should_emit_tool_activity(
        self,
        tool_call_id: str,
        *,
        status: str,
        detail: str,
    ) -> bool:
        if not tool_call_id:
            return True
        with self._lock:
            previous = self._tool_activity_state.get(tool_call_id)
            current = (status, detail)
            if previous == current:
                return False
            self._tool_activity_state[tool_call_id] = current
        return True


__all__ = [
    "GrokAcpTurnProjectionMixin",
    "grok_tool_activity",
    "tool_category",
]
