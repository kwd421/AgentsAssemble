import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import RoomInviteModal from "./RoomInviteModal";

afterEach(cleanup);

function renderInviteModal({ publicAccess = true } = {}) {
  const onGenerateSecureInvite = vi.fn();
  const onGenerateAgentInvite = vi.fn();
  render(
    <RoomInviteModal
      roomLabel="제품 방"
      secureInviteUrl=""
      agentInviteUrl=""
      operatorPairingUrl=""
      localPreviewUrl="http://127.0.0.1:8765/join"
      publicUrl={publicAccess ? "https://room.example.com" : ""}
      publicUrlDraft=""
      tunnelStatus={{
        running: publicAccess,
        phase: publicAccess ? "running" : "stopped",
        public_url: publicAccess ? "https://room.example.com" : "",
      }}
      friends={[]}
      onClose={vi.fn()}
      onGenerateSecureInvite={onGenerateSecureInvite}
      onCopy={vi.fn()}
      onGenerateAgentInvite={onGenerateAgentInvite}
      onCopyAgentInvite={vi.fn()}
      onGenerateOperatorPairing={vi.fn()}
      onCopyOperatorPairing={vi.fn()}
      onCopyLocalPreview={vi.fn()}
      onPublicUrlDraftChange={vi.fn()}
      onConfigurePublicUrl={vi.fn()}
      onHostTokenDraftChange={vi.fn()}
      onSaveHostToken={vi.fn()}
      onStartTunnel={vi.fn()}
      onStopTunnel={vi.fn()}
      onInviteFriend={vi.fn()}
    />
  );
  return { onGenerateSecureInvite, onGenerateAgentInvite };
}

describe("RoomInviteModal", () => {
  it("creates a human invite with the selected use limit and lifetime", () => {
    const { onGenerateSecureInvite } = renderInviteModal();

    fireEvent.change(screen.getByLabelText("초대 가능 인원"), { target: { value: "5" } });
    fireEvent.change(screen.getByLabelText("링크 유효시간"), {
      target: { value: "604800" },
    });
    fireEvent.click(screen.getByRole("button", { name: "사람 초대 링크 생성" }));

    expect(onGenerateSecureInvite).toHaveBeenCalledWith(
      { maxUses: 5, ttlSeconds: 604800 },
      false
    );
  });

  it("requires an explicit public-access confirmation for an external AI session", () => {
    const { onGenerateAgentInvite } = renderInviteModal({ publicAccess: false });

    expect(screen.getByRole("heading", { name: "외부 AI 세션 초대" })).toBeTruthy();
    fireEvent.click(
      screen.getByRole("button", { name: "외부 AI 세션 초대 링크 생성" })
    );

    expect(onGenerateAgentInvite).not.toHaveBeenCalled();
    expect(screen.getByRole("alertdialog", { name: "외부 접속을 열까요?" })).toBeTruthy();
    fireEvent.click(
      screen.getByRole("button", { name: "외부 접속 열고 링크 만들기" })
    );

    expect(onGenerateAgentInvite).toHaveBeenCalledWith(true);
  });
});
