import { createRef } from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Bot } from "lucide-react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { LiveAgent, RoomAgentSession } from "../../../api";
import AgentIdentitySettings from "./AgentIdentitySettings";
import type { MemberEntry } from "./memberTypes";

const apiMocks = vi.hoisted(() => ({
  uploadLobbyAttachment: vi.fn(),
}));

vi.mock("../../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../../api")>();
  return {
    ...actual,
    uploadLobbyAttachment: apiMocks.uploadLobbyAttachment,
  };
});

vi.mock("../ImageCropper", () => ({
  default: ({
    file,
    onCropped,
  }: {
    file: File;
    onCropped: (file: File) => void;
  }) => (
    <button type="button" onClick={() => onCropped(file)}>
      테스트 이미지 적용
    </button>
  ),
}));

const AGENT: LiveAgent = {
  agent_id: "agent-1",
  display_name: "Agent One",
  status: "online",
  provider_kind: "codex",
  engagement_mode: "agent_session",
  meeting_id: "room-a",
  last_seen_at: "",
  last_reply_at: "",
  sandbox_enforcement: "read-only",
  capabilities: [],
};

const ENTRY: MemberEntry = {
  id: "agent-1",
  agent: AGENT,
  displayName: "Agent One",
  detail: "Codex",
  role: "agent",
  owner: true,
  ownedByViewer: true,
  active: true,
  muted: false,
  meetingId: "room-a",
  canViewQuota: false,
  icon: Bot,
};

const SESSION: RoomAgentSession = {
  room_id: "room-a",
  session_id: "agent-1",
  participant_id: "agent-1",
  display_name: "Agent One",
  status: "stopped",
  runtime_status: "stopped",
  enabled: true,
  provider_kind: "codex",
  runtime_kind: "codex_app_server",
  connection_kind: "agent_session",
  model: "gpt-5.6-luna",
};

describe("AgentIdentitySettings", () => {
  beforeEach(() => {
    apiMocks.uploadLobbyAttachment.mockReset();
  });

  it("opens the shared cropper from the visible avatar and saves the crop canonically", async () => {
    const avatarFile = new File(["avatar"], "avatar.png", { type: "image/png" });
    const avatarInputRef = createRef<HTMLInputElement>();
    const onAgentConfigure = vi.fn().mockResolvedValue(undefined);
    apiMocks.uploadLobbyAttachment.mockResolvedValue({
      id: "avatar-12345678",
      filename: "avatar.png",
      content_type: "image/png",
      size: 6,
      is_image: true,
      url: "/api/attachments/avatar-12345678?view=1",
      download_url: "/api/attachments/avatar-12345678?download=1",
    });

    render(
      <>
        <button type="button" onClick={() => avatarInputRef.current?.click()}>
          Agent One 프로필 사진 편집
        </button>
        <AgentIdentitySettings
          entry={{ ...ENTRY, agentSession: SESSION }}
          agent={AGENT}
          avatarInputRef={avatarInputRef}
          roomSessionToken="paired-operator-session"
          onAgentConfigure={onAgentConfigure}
        />
      </>
    );

    fireEvent.click(screen.getByRole("button", { name: "Agent One 프로필 사진 편집" }));
    fireEvent.change(screen.getByLabelText("에이전트 프로필 사진 선택"), {
      target: { files: [avatarFile] },
    });
    fireEvent.click(screen.getByRole("button", { name: "테스트 이미지 적용" }));

    await waitFor(() =>
      expect(apiMocks.uploadLobbyAttachment).toHaveBeenCalledWith(avatarFile, {
        purpose: "profile_avatar",
        sessionToken: "paired-operator-session",
      })
    );
    await waitFor(() =>
      expect(onAgentConfigure).toHaveBeenCalledWith(SESSION, {
        display_name: "Agent One",
        avatar_image_url: "/api/attachments/avatar-12345678?view=1",
      })
    );
    expect(screen.getByText("프로필 사진 저장됨")).toBeTruthy();
  });
});
