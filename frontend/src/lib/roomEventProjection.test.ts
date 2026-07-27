import { describe, expect, it } from "vitest";

import type { RoomEvent } from "../api";
import { projectRoomEventProgress, projectRoomEventsToTimeline } from "./roomEventProjection";

function event(overrides: Partial<RoomEvent>): RoomEvent {
  return {
    v: 1,
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
    const attachment = {
      id: "attachment-1",
      filename: "photo.png",
      content_type: "image/png",
      size: 42,
      is_image: true,
      url: "/api/attachments/attachment-1?view=1",
      download_url: "/api/attachments/attachment-1?download=1",
    };
    const timeline = projectRoomEventsToTimeline([
      event({ id: "d1", seq: 1, type: "message_delta", turn_id: "turn-1", content: "hello " }),
      event({ id: "d2", seq: 2, type: "message_delta", turn_id: "turn-1", content: "world" }),
      event({
        id: "f1",
        seq: 3,
        type: "message_final",
        turn_id: "turn-1",
        content: "hello world",
        avatar_image_url: "/api/attachments/codex-avatar",
        provider_kind: "codex_app_server",
        attachments: [attachment],
      }),
    ]);

    expect(timeline).toHaveLength(1);
    expect(timeline[0]).toMatchObject({
      id: "turn-1",
      message: "hello world",
      flow_action: "message_final",
      attachments: [attachment],
      avatar_image_url: "/api/attachments/codex-avatar",
      provider_kind: "codex_app_server",
    });
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
            providerKind: "antigravity_live_session",
          },
        },
      }
    );

    expect(timeline[0]).toMatchObject({
      name: "Makima",
      avatar_image_url: "/api/attachments/makima-avatar",
      provider_kind: "antigravity_live_session",
    });
  });

  it("does not revive an event-time avatar after the canonical avatar is cleared", () => {
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
            avatarImageUrl: undefined,
          },
        },
      }
    );

    expect(timeline[0].name).toBe("Makima");
    expect(timeline[0].avatar_image_url).toBeUndefined();
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

  it("updates one reasoning step across an OpenCode answer", () => {
    const timeline = projectRoomEventsToTimeline([
      event({
        id: "reasoning-running",
        seq: 1,
        type: "activity_delta",
        turn_id: "turn-opencode",
        activity_kind: "reasoning",
        category: "reasoning",
        status: "running",
        content: "생각 정리 중",
      }),
      event({
        id: "answer-delta",
        seq: 2,
        type: "message_delta",
        turn_id: "turn-opencode",
        content: "답변",
      }),
      event({
        id: "reasoning-completed",
        seq: 3,
        type: "activity_delta",
        turn_id: "turn-opencode",
        activity_kind: "reasoning",
        category: "reasoning",
        status: "completed",
        content: "생각 정리 완료",
      }),
      event({
        id: "answer-final",
        seq: 4,
        type: "message_final",
        turn_id: "turn-opencode",
        content: "답변입니다.",
      }),
    ]);

    expect(timeline).toHaveLength(2);
    expect(timeline[0]).toMatchObject({
      id: "reasoning-running",
      kind: "thinking",
      message: "생각 정리 완료",
      flow_id: "turn-opencode",
    });
    expect(timeline[1]).toMatchObject({
      id: "turn-opencode",
      kind: "message",
      message: "답변입니다.",
    });
  });
});
