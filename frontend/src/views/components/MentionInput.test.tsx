import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import MentionInput from "./MentionInput";

describe("MentionInput", () => {
  afterEach(() => cleanup());

  it("shows the participant avatar and owner beside a mention candidate", () => {
    render(
      <MentionInput
        value="@"
        onChange={vi.fn()}
        ariaLabel="채팅 입력"
        mentionables={[
          {
            token: "agent-uid-123",
            label: "Opus 5",
            avatarImage: "/api/avatars/opus.png",
            detail: "SeiNel의 에이전트",
            participantKind: "agent",
            providerKind: "claude_code",
          },
        ]}
      />
    );

    fireEvent.select(screen.getByLabelText("채팅 입력"), {
      target: { selectionStart: 1 },
    });

    const candidate = screen.getByRole("option", {
      name: "Opus 5, SeiNel의 에이전트",
    });
    expect(candidate.querySelector("img")?.getAttribute("src")).toBe(
      "/api/avatars/opus.png"
    );
  });
});
