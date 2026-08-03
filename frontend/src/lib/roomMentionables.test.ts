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
      { token: "operator-local", label: "나" },
      {
        token: "codex-codex-gpt-5.6-luna",
        label: "Luna — 플레이어",
      },
    ]);
    expect(
      mentionOptions(mentionables, mentionQueryAtCursor("@luna"))
    ).toEqual([
      {
        token: "codex-codex-gpt-5.6-luna",
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
      { token: "host", label: "나" },
      { token: "alpha", label: "동일 이름 · alpha" },
      { token: "bravo", label: "동일 이름 · bravo" },
      { token: "charlie", label: "charlie" },
    ]);
  });

  it("inserts a participant id while preserving a spaced display label", () => {
    const expectedMention = "<@sol-dm> ";
    const mentionable = roomMentionables({
      viewerParticipantId: "host",
      agents: [{ agent_id: "sol-dm", display_name: "Sol — 던전 마스터" }],
      members: [],
    })[1];

    expect(mentionable).toEqual({
      token: "sol-dm",
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
      message: expectedMention,
      cursor: expectedMention.length,
    });
  });
});
