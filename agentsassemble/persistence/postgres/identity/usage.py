"""PostgreSQL operations for identity-owned provider usage accounting."""
from __future__ import annotations

from psycopg import Connection

from agentsassemble.room.text import clean_room_text


def record_usage(connection: Connection, event: dict[str, object], *, now: str) -> None:
    def non_negative_int(value: object) -> int:
        try:
            return max(0, int(value))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return 0

    connection.execute(
        """INSERT INTO identity_usage_events(
               created_at, user_id, participant_id, meeting_id, provider,
               model, input_tokens, output_tokens, cost_owner, estimated
           ) VALUES(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (
            str(event.get("created_at") or now),
            clean_room_text(event.get("user_id"), limit=128),
            clean_room_text(event.get("participant_id"), limit=128),
            clean_room_text(event.get("meeting_id"), limit=128),
            clean_room_text(event.get("provider"), limit=64),
            clean_room_text(event.get("model"), limit=128),
            non_negative_int(event.get("input_tokens")),
            non_negative_int(event.get("output_tokens")),
            clean_room_text(event.get("cost_owner"), limit=32),
            bool(event.get("estimated")),
        ),
    )


def usage_summary(
    connection: Connection,
    *,
    user_id: str = "",
    meeting_id: str = "",
    since: str = "",
) -> dict[str, object]:
    where: list[str] = []
    parameters: list[object] = []
    if user_id:
        where.append("user_id = %s")
        parameters.append(clean_room_text(user_id, limit=128))
    if meeting_id:
        where.append("meeting_id = %s")
        parameters.append(clean_room_text(meeting_id, limit=128))
    if since:
        where.append("created_at >= %s")
        parameters.append(str(since))
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    totals = connection.execute(
        """SELECT COUNT(*) AS events,
                  COALESCE(SUM(input_tokens), 0) AS input_tokens,
                  COALESCE(SUM(output_tokens), 0) AS output_tokens,
                  COALESCE(SUM(CASE WHEN estimated THEN 1 ELSE 0 END), 0)
                      AS estimated_events
           FROM identity_usage_events"""
        + clause,
        tuple(parameters),
    ).fetchone()
    rows = connection.execute(
        """SELECT provider, model, COUNT(*) AS events,
                  COALESCE(SUM(input_tokens), 0) AS input_tokens,
                  COALESCE(SUM(output_tokens), 0) AS output_tokens
           FROM identity_usage_events"""
        + clause
        + " GROUP BY provider, model ORDER BY (SUM(input_tokens) + SUM(output_tokens)) DESC",
        tuple(parameters),
    ).fetchall()
    return {
        "events": int(totals["events"]),
        "input_tokens": int(totals["input_tokens"]),
        "output_tokens": int(totals["output_tokens"]),
        "estimated_events": int(totals["estimated_events"]),
        "by_model": [
            {
                "provider": str(row["provider"]),
                "model": str(row["model"]),
                "events": int(row["events"]),
                "input_tokens": int(row["input_tokens"]),
                "output_tokens": int(row["output_tokens"]),
            }
            for row in rows
        ],
    }
