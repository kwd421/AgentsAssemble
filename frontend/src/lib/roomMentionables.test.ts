import { describe, expect, it } from "vitest";
import { mentionOptions, mentionQueryAtCursor } from "./mentionComposerModel";
import { roomMentionables } from "./roomMentionables";

describe("roomMentionables", () => {
  it("shows one display-name option per participant instead of a second id option", () => {
    const mentionables = roomMentionables({
        viewerParticipantId: "operator-local",
        agents: [
          {
            agent_id: "codex-codex-gpt-5.6-luna",
            display_name: "Luna — 플레이어",
          },
        ],
        members: [
          {
            participant_id: "operator-local",
            display_name: "호스트",
          },
          {
            participant_id: "codex-codex-gpt-5.6-luna",
            display_name: "Luna — 플레이어",
          },
        ],
      });

    expect(mentionables).toEqual(["나", "Luna — 플레이어"]);
    expect(
      mentionOptions(mentionables, mentionQueryAtCursor("@luna"))
    ).toEqual(["Luna — 플레이어"]);
  });

  it("falls back to ids when names are absent or collide", () => {
    expect(
      roomMentionables({
        viewerParticipantId: "host",
        agents: [
          { agent_id: "alpha", display_name: "동일 이름" },
          { agent_id: "bravo", display_name: "동일 이름" },
          { agent_id: "charlie" },
        ],
        members: [],
      })
    ).toEqual(["나", "동일 이름", "bravo", "charlie"]);
  });
});
