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

  it("folds edit and delete events into the original canonical message", () => {
    const updated = projectRoomEventsToTimeline([
      event({ id: "message-1", content: "draft" }),
      event({
        id: "edit-1",
        seq: 2,
        type: "message_updated",
        target_event_id: "message-1",
        content: "final",
        edited_at: "2026-01-01T00:01:00Z",
      }),
    ]);

    expect(updated).toHaveLength(1);
    expect(updated[0]).toMatchObject({
      record_id: "message-1",
      message: "final",
      edited_at: "2026-01-01T00:01:00Z",
    });

    const deleted = projectRoomEventsToTimeline([
      event({ id: "message-1", content: "private", attachments: [] }),
      event({
        id: "delete-1",
        seq: 2,
        type: "message_deleted",
        target_event_id: "message-1",
      }),
    ]);

    expect(deleted).toHaveLength(1);
    expect(deleted[0]).toMatchObject({
      record_id: "message-1",
      message_deleted: true,
      attachments: [],
    });
    expect(deleted[0].message).not.toContain("private");
  });

  it("keeps one tombstone and removes ballot rows when a vote is deleted", () => {
    const timeline = projectRoomEventsToTimeline([
      event({
        id: "vote-1",
        actor: { participant_id: "host-1", participant_type: "human" },
        message_kind: "vote",
        vote_question: "Ship it?",
        vote_options: ["Yes", "No"],
      }),
      event({
        id: "ballot-1",
        seq: 2,
        actor: { participant_id: "guest-1", participant_type: "human" },
        message_kind: "vote_cast",
        vote_id: "vote-1",
        vote_choice: "Yes",
      }),
      event({
        id: "delete-vote-1",
        seq: 3,
        type: "message_deleted",
        target_event_id: "vote-1",
      }),
    ]);

    expect(timeline).toHaveLength(1);
    expect(timeline[0]).toMatchObject({
      record_id: "vote-1",
      kind: "vote",
      message_deleted: true,
    });

    const boundedHistory = projectRoomEventsToTimeline([
      event({
        id: "ballot-2",
        seq: 100,
        actor: { participant_id: "guest-2", participant_type: "human" },
        message_kind: "vote_cast",
        vote_id: "old-vote",
        vote_choice: "No",
      }),
      event({
        id: "delete-old-vote",
        seq: 101,
        type: "message_deleted",
        target_event_id: "old-vote",
      }),
    ]);

    expect(boundedHistory).toEqual([]);
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
      {
        viewerParticipantId: "guest-1",
        participantProfiles: { "guest-1": { displayName: "Guest" } },
      }
    );

    expect(timeline[0]).toMatchObject({ name: "Guest", side: "mine" });
  });

  it("does not render internal turn state and finishes progress on final", () => {
    const state = event({ type: "turn_state", phase: "thinking", turn_id: "turn-1" });
    expect(projectRoomEventsToTimeline([state])).toEqual([]);
    expect(projectRoomEventProgress(state)?.message).toBe("생각 중...");
    expect(projectRoomEventProgress(event({ type: "message_final", turn_id: "turn-1" }))).toBeNull();
  });

  it("keeps the canonical poll id when a provider turn groups the visible card", () => {
    const timeline = projectRoomEventsToTimeline([
      event({
        id: "vote-1",
        turn_id: "turn-provider-1",
        actor: { participant_id: "operator-local", participant_type: "human" },
        content: "",
        message_kind: "vote",
        vote_question: "어느 길로 갈까?",
        vote_options: ["북쪽", "남쪽"],
        vote_duration_seconds: 900,
        vote_deadline_at: "2026-01-01T00:15:00Z",
      }),
    ]);

    expect(timeline).toEqual([
      expect.objectContaining({
        id: "turn-provider-1",
        kind: "vote",
        vote_id: "vote-1",
        vote_question: "어느 길로 갈까?",
        vote_options: ["북쪽", "남쪽"],
        vote_duration_seconds: 900,
        vote_deadline_at: "2026-01-01T00:15:00Z",
      }),
    ]);
  });

  it("does not project an individual anonymous ballot into the room timeline", () => {
    const timeline = projectRoomEventsToTimeline([
      event({
        id: "ballot-1",
        actor: { participant_id: "voter-1", participant_type: "human" },
        display_name: "민지",
        content: "",
        message_kind: "vote_cast",
        vote_id: "vote-1",
        vote_choice: "남쪽",
      }),
    ]);

    expect(timeline).toEqual([]);
  });

  it("keeps provider failures out of the public conversation timeline", () => {
    expect(
      projectRoomEventsToTimeline([
        event({
          id: "error-1",
          type: "error",
          content: "provider transport failed",
        }),
      ])
    ).toEqual([]);
    expect(projectRoomEventProgress(event({ type: "error" }))).toBeNull();
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

  it("updates one structured tool activity from running to completed", () => {
    const running = event({
      id: "activity-running",
      type: "activity_delta",
      turn_id: "turn-1",
      activity_kind: "tool",
      activity_id: "search-1",
      activity_title: "WebSearch",
      activity_detail: "Alabasta strongest character",
      category: "search",
      status: "running",
      content: "Alabasta strongest character",
    });
    const completed = event({
      ...running,
      id: "activity-completed",
      seq: 2,
      status: "completed",
    });
    const timeline = projectRoomEventsToTimeline([running, completed]);

    expect(timeline).toHaveLength(1);
    expect(timeline[0]).toMatchObject({
      kind: "thinking",
      message: "Alabasta strongest character",
      flow_action: "activity_delta",
      activity_id: "search-1",
      activity_title: "WebSearch",
      activity_detail: "Alabasta strongest character",
      activity_category: "search",
      activity_status: "completed",
    });
    expect(projectRoomEventProgress(running)?.message).toBe("Alabasta strongest character");
  });

  it("projects compaction as transient progress instead of permanent thought history", () => {
    const started = event({
      id: "compact-started",
      type: "activity_delta",
      turn_id: "turn-compact",
      activity_kind: "compaction",
      category: "compaction",
      status: "started",
      content: "압축 중...",
    });
    const completed = event({
      ...started,
      id: "compact-completed",
      status: "completed",
      content: "압축 완료",
    });

    expect(projectRoomEventsToTimeline([started, completed])).toEqual([]);
    expect(projectRoomEventProgress(started)).toMatchObject({
      turnId: "turn-compact",
      activity: "compacting",
    });
    expect(projectRoomEventProgress(completed)).toBeNull();
  });

  it("uses the canonical room role when projecting a message author", () => {
    const timeline = projectRoomEventsToTimeline(
      [
        event({
          actor: { participant_id: "terra", participant_type: "agent" },
          participant_id: "terra",
          content: "다음 장면입니다.",
        }),
      ],
      { participantProfiles: { terra: { displayName: "Terra DM", role: "director" } } }
    );

    expect(timeline[0]).toMatchObject({
      name: "Terra DM",
      role: "director",
    });
  });

  it("updates one reasoning step across an OpenCode answer", () => {
    const timeline = projectRoomEventsToTimeline([
      event({
        id: "reasoning-running",
        seq: 1,
        type: "activity_delta",
        turn_id: "turn-opencode",
        activity_kind: "reasoning",
        activity_id: "reasoning-1",
        activity_detail: "두 후보의 근거를 비교하고 있습니다.",
        category: "reasoning",
        status: "running",
        content: "두 후보의 근거를 비교하고 있습니다.",
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
        activity_id: "reasoning-1",
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
      message: "두 후보의 근거를 비교하고 있습니다.",
      flow_id: "turn-opencode",
      activity_status: "completed",
    });
    expect(timeline[1]).toMatchObject({
      id: "turn-opencode",
      kind: "message",
      message: "답변입니다.",
    });
  });
});
