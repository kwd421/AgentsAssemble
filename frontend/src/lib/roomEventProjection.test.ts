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

  it("renders provider-visible thinking as a collapsible timeline step", () => {
    const timeline = projectRoomEventsToTimeline(
      [
        event({
          id: "thought-1",
          type: "thinking_delta",
          turn_id: "turn-1",
          content: "검색 결과를 비교하는 중",
        }),
        event({
          id: "final-1",
          seq: 2,
          type: "message_final",
          turn_id: "turn-1",
          content: "결론입니다.",
        }),
      ],
      { participantProfiles: { codex: { displayName: "루나" } } }
    );

    expect(timeline).toHaveLength(2);
    expect(timeline[0]).toMatchObject({
      kind: "thinking",
      name: "루나",
      message: "검색 결과를 비교하는 중",
      flow_id: "turn-1",
    });
    expect(timeline[1]).toMatchObject({ kind: "message", name: "루나" });
    expect(projectRoomEventProgress(event({ type: "thinking_delta", content: "검토 중" }))?.message).toBe(
      "검토 중"
    );
  });

  it("uses the current participant profile for historical messages", () => {
    const timeline = projectRoomEventsToTimeline(
      [
        event({
          display_name: "Antigravity CLI",
          avatar_image_url: "/api/attachments/old-avatar",
          content: "안녕하세요.",
        }),
      ],
      {
        participantProfiles: {
          codex: {
            displayName: "Makima",
            avatarImageUrl: "/api/attachments/makima-avatar",
          },
        },
      }
    );

    expect(timeline[0]).toMatchObject({
      name: "Makima",
      avatar_image_url: "/api/attachments/makima-avatar",
    });
  });

  it("keeps the event author snapshot only when the participant is unavailable", () => {
    const timeline = projectRoomEventsToTimeline([
      event({ display_name: "Imported Agent", content: "legacy" }),
    ]);

    expect(timeline[0].name).toBe("Imported Agent");
  });

  it("renders sanitized tool activity in the same collapsible timeline", () => {
    const activity = event({
      id: "activity-1",
      type: "activity_delta",
      turn_id: "turn-1",
      activity_kind: "tool",
      category: "search",
      status: "running",
      content: "정보 검색 중",
    });

    expect(projectRoomEventsToTimeline([activity])[0]).toMatchObject({
      kind: "thinking",
      message: "정보 검색 중",
      flow_action: "activity_delta",
    });
    expect(projectRoomEventProgress(activity)?.message).toBe("정보 검색 중");
  });
});
