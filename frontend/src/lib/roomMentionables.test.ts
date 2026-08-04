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
            avatar_image_url: "/api/avatar/luna.png",
            owner_id: "operator-local",
            provider_kind: "codex_live_session",
          },
        ],
        members: [
          {
            participant_id: "operator-local",
            display_name: "SeiNel",
            participant_type: "human",
          },
          {
            participant_id: "codex-codex-gpt-5.6-luna",
            display_name: "Luna — 플레이어",
          },
        ],
      });

    expect(mentionables).toEqual([
      {
        token: "operator-local",
        label: "SeiNel",
        avatarImage: undefined,
        participantKind: "human",
        detail: "사람",
      },
      {
        token: "codex-codex-gpt-5.6-luna",
        label: "Luna — 플레이어",
        avatarImage: "/api/avatar/luna.png",
        participantKind: "agent",
        providerKind: "codex_live_session",
        detail: "SeiNel의 에이전트",
      },
    ]);
    expect(
      mentionOptions(mentionables, mentionQueryAtCursor("@luna"))[0]
    ).toMatchObject({
      token: "codex-codex-gpt-5.6-luna",
      label: "Luna — 플레이어",
      detail: "SeiNel의 에이전트",
    });
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
      }).map(({ token, label }) => ({ token, label }))
    ).toEqual([
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
    })[0];

    expect(mentionable).toMatchObject({
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
