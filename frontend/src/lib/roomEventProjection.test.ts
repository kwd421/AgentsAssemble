import { describe, expect, it } from "vitest";

import type { RoomEvent } from "../api";
import { projectRoomEventProgress, projectRoomEventsToTimeline } from "./roomEventProjection";

function event(overrides: Partial<RoomEvent>): RoomEvent {
  return {
    id: "event-1",
    seq: 1,
    created_at: "2026-01-01T00:00:00Z",
    room_id: "general",
    type: "message_final",
    actor: { participant_id: "codex", participant_type: "agent" },
    ...overrides,
  };
}

describe("projectRoomEventsToTimeline", () => {
  it("updates one bubble across multiple deltas and the final message", () => {
    const timeline = projectRoomEventsToTimeline([
      event({ id: "d1", seq: 1, type: "message_delta", turn_id: "turn-1", content: "hello " }),
      event({ id: "d2", seq: 2, type: "message_delta", turn_id: "turn-1", content: "world" }),
      event({ id: "f1", seq: 3, type: "message_final", turn_id: "turn-1", content: "hello world" }),
    ]);

    expect(timeline).toHaveLength(1);
    expect(timeline[0]).toMatchObject({ id: "turn-1", message: "hello world", flow_action: "message_final" });
  });

  it("groups legacy delta and final events by source event and actor", () => {
    const timeline = projectRoomEventsToTimeline([
      event({ id: "legacy-delta", seq: 1, type: "message_delta", source_event_id: "human-1", content: "clean" }),
      event({ id: "legacy-final", seq: 2, type: "message_final", source_event_id: "human-1", content: "clean final" }),
    ]);

    expect(timeline).toHaveLength(1);
    expect(timeline[0].message).toBe("clean final");
  });

  it("uses the authenticated viewer identity for own-message styling", () => {
    const timeline = projectRoomEventsToTimeline(
      [event({ actor: { participant_id: "guest-1", participant_type: "human" }, content: "mine" })],
      { viewerParticipantId: "guest-1" }
    );

    expect(timeline[0]).toMatchObject({ name: "나", side: "mine" });
  });

  it("does not render internal turn state and finishes progress on final", () => {
    const state = event({ type: "turn_state", phase: "thinking", turn_id: "turn-1" });
    expect(projectRoomEventsToTimeline([state])).toEqual([]);
    expect(projectRoomEventProgress(state)?.message).toBe("생각 중...");
    expect(projectRoomEventProgress(event({ type: "message_final", turn_id: "turn-1" }))).toBeNull();
  });
});
