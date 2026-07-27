import { describe, expect, it } from "vitest";
import {
  insertMentionText,
  mentionOptions,
  mentionQueryAtCursor,
} from "./mentionComposerModel";
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

    expect(mentionables).toEqual([
      { token: "나", label: "나" },
      {
        token: "Luna",
        label: "Luna — 플레이어",
      },
    ]);
    expect(
      mentionOptions(mentionables, mentionQueryAtCursor("@luna"))
    ).toEqual([
      {
        token: "Luna",
        label: "Luna — 플레이어",
      },
    ]);
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
    ).toEqual([
      { token: "나", label: "나" },
      { token: "alpha", label: "동일 이름 · alpha" },
      { token: "bravo", label: "동일 이름 · bravo" },
      { token: "charlie", label: "charlie" },
    ]);
  });

  it("inserts the unique participant id even when the visible label contains spaces", () => {
    const mentionable = roomMentionables({
      viewerParticipantId: "host",
      agents: [{ agent_id: "sol-dm", display_name: "Sol — 던전 마스터" }],
      members: [],
    })[1];

    expect(mentionable).toEqual({
      token: "Sol",
      label: "Sol — 던전 마스터",
    });
    expect(
      insertMentionText(
        "@sol",
        4,
        mentionQueryAtCursor("@sol"),
        mentionable
      )
    ).toEqual({
      message: "@Sol ",
      cursor: 5,
    });
  });
});
