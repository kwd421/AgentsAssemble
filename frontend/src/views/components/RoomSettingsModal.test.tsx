import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Radio } from "lucide-react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DEFAULT_ROOM_APPEARANCE } from "../../lib/roomAppearance";
import RoomSettingsModal from "./RoomSettingsModal";

afterEach(cleanup);

const apiMocks = vi.hoisted(() => ({
  uploadLobbyAttachment: vi.fn(),
}));

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return {
    ...actual,
    uploadLobbyAttachment: apiMocks.uploadLobbyAttachment,
  };
});

const room = {
  id: "server-general",
  label: "General",
  meetingId: "general",
  topic: "",
  shortLabel: "G",
  icon: Radio,
  createdAt: "2026-07-14T00:00:00Z",
  tone: "resident" as const,
};

function renderSettings(
  conversationMode: "ordered" | "ambient" | "continuous" | null,
  onConversationModeChange = vi.fn(),
  settingsStatus: "loading" | "ready" | "saving" | "stale" | "error" = "ready",
  onRetrySettings = vi.fn(),
  onOrderedExcludePreviousSpeakerChange = vi.fn(),
  onAppearanceChange = vi.fn().mockResolvedValue(undefined),
  onToolModeChange = vi.fn()
) {
  render(
    <RoomSettingsModal
      room={room}
      appearance={DEFAULT_ROOM_APPEARANCE}
      channelSettings={{}}
      settingsStatus={settingsStatus}
      settingsError={settingsStatus === "error" ? "offline" : ""}
      conversationMode={conversationMode}
      toolMode={conversationMode ? "chat" : null}
      orderedExcludePreviousSpeaker={conversationMode ? true : null}
      maxRelayTurns={conversationMode ? 6 : null}
      canInvite
      onClose={() => undefined}
      onInvite={() => undefined}
      onRoomChange={() => undefined}
      onAppearanceChange={onAppearanceChange}
      onChannelSettingChange={() => undefined}
      onConversationModeChange={onConversationModeChange}
      onToolModeChange={onToolModeChange}
      onOrderedExcludePreviousSpeakerChange={onOrderedExcludePreviousSpeakerChange}
      onMaxRelayTurnsChange={() => undefined}
      onRetrySettings={onRetrySettings}
      onDeleteRoom={async () => undefined}
    />
  );
  return onConversationModeChange;
}

describe("RoomSettingsModal conversation mode", () => {
  beforeEach(() => {
    apiMocks.uploadLobbyAttachment.mockReset();
  });

  it("binds a banner upload to the room and waits for canonical appearance persistence", async () => {
    const onAppearanceChange = vi.fn().mockResolvedValue(undefined);
    apiMocks.uploadLobbyAttachment.mockResolvedValue({
      id: "banner-asset",
      filename: "banner.png",
      content_type: "image/png",
      size: 3,
      is_image: true,
      url: "/api/attachments/banner-asset?view=1",
      download_url: "/api/attachments/banner-asset?download=1",
    });
    renderSettings(
      "ordered",
      vi.fn(),
      "ready",
      vi.fn(),
      vi.fn(),
      onAppearanceChange
    );

    const file = new File(["png"], "banner.png", { type: "image/png" });
    await userEvent.upload(screen.getByLabelText("배너 이미지"), file);

    expect(apiMocks.uploadLobbyAttachment).toHaveBeenCalledWith(file, {
      roomId: "general",
      purpose: "room_appearance",
    });
    expect(onAppearanceChange).toHaveBeenCalledWith({
      bannerImage: "/api/attachments/banner-asset?view=1",
      bannerPreset: "custom",
    });
    expect(await screen.findByText("배너 이미지 저장됨")).toBeTruthy();
  });

  it("lets the user activate ambient discussion from the room settings UI", async () => {
    const onConversationModeChange = renderSettings("ordered");

    await userEvent.click(screen.getByRole("radio", { name: /자유 토론/ }));

    expect(onConversationModeChange).toHaveBeenCalledWith("ambient");
  });

  it("lets the host enable tabletop tools independently of conversation order", async () => {
    const onToolModeChange = vi.fn();
    renderSettings(
      "ordered",
      vi.fn(),
      "ready",
      vi.fn(),
      vi.fn(),
      vi.fn().mockResolvedValue(undefined),
      onToolModeChange
    );

    await userEvent.click(screen.getByRole("radio", { name: /테이블탑/ }));

    expect(onToolModeChange).toHaveBeenCalledWith("tabletop");
  });

  it("lets the user allow the previous speaker in general ordered selection", async () => {
    const onChange = vi.fn();
    renderSettings("ordered", vi.fn(), "ready", vi.fn(), onChange);

    await userEvent.click(
      screen.getByRole("checkbox", { name: /직전 발언자 연속 선택 방지/ })
    );

    expect(onChange).toHaveBeenCalledWith(false);
  });

  it("shows legacy continuous mode only for a room that already uses it", () => {
    const { rerender } = render(
      <RoomSettingsModal
        room={room}
        appearance={DEFAULT_ROOM_APPEARANCE}
        channelSettings={{}}
        settingsStatus="ready"
        settingsError=""
        conversationMode="ordered"
        toolMode="chat"
        orderedExcludePreviousSpeaker
        maxRelayTurns={6}
        canInvite
        onClose={() => undefined}
        onInvite={() => undefined}
        onRoomChange={() => undefined}
        onAppearanceChange={async () => undefined}
        onChannelSettingChange={() => undefined}
        onConversationModeChange={() => undefined}
        onToolModeChange={() => undefined}
        onOrderedExcludePreviousSpeakerChange={() => undefined}
        onMaxRelayTurnsChange={() => undefined}
        onRetrySettings={() => undefined}
        onDeleteRoom={async () => undefined}
      />
    );

    expect(screen.queryByRole("radio", { name: /기존 연쇄 대화/ })).toBeNull();

    rerender(
      <RoomSettingsModal
        room={room}
        appearance={DEFAULT_ROOM_APPEARANCE}
        channelSettings={{}}
        settingsStatus="ready"
        settingsError=""
        conversationMode="continuous"
        toolMode="chat"
        orderedExcludePreviousSpeaker
        maxRelayTurns={6}
        canInvite
        onClose={() => undefined}
        onInvite={() => undefined}
        onRoomChange={() => undefined}
        onAppearanceChange={async () => undefined}
        onChannelSettingChange={() => undefined}
        onConversationModeChange={() => undefined}
        onToolModeChange={() => undefined}
        onOrderedExcludePreviousSpeakerChange={() => undefined}
        onMaxRelayTurnsChange={() => undefined}
        onRetrySettings={() => undefined}
        onDeleteRoom={async () => undefined}
      />
    );

    expect(
      (screen.getByRole("radio", { name: /기존 연쇄 대화/ }) as HTMLInputElement).checked
    ).toBe(true);
  });

  it("does not guess routing settings when the server read fails", async () => {
    const onConversationModeChange = vi.fn();
    const onRetrySettings = vi.fn();
    renderSettings(null, onConversationModeChange, "error", onRetrySettings);

    const ordered = screen.getByRole("radio", { name: /순서/ }) as HTMLInputElement;
    expect(ordered.checked).toBe(false);
    expect(ordered.disabled).toBe(true);
    expect(screen.getByRole("alert").textContent).toContain("확인할 수 없어");

    await userEvent.click(screen.getByRole("button", { name: "다시 불러오기" }));

    expect(onRetrySettings).toHaveBeenCalledTimes(1);
    expect(onConversationModeChange).not.toHaveBeenCalled();
  });

  it("offers channel notification controls only for current navigable room channels", async () => {
    const onChannelSettingChange = vi.fn();
    render(
      <RoomSettingsModal
        room={room}
        appearance={DEFAULT_ROOM_APPEARANCE}
        channelSettings={{}}
        settingsStatus="ready"
        settingsError=""
        conversationMode="ordered"
        toolMode="chat"
        orderedExcludePreviousSpeaker
        maxRelayTurns={6}
        canInvite
        onClose={() => undefined}
        onInvite={() => undefined}
        onRoomChange={() => undefined}
        onAppearanceChange={async () => undefined}
        onChannelSettingChange={onChannelSettingChange}
        onConversationModeChange={() => undefined}
        onToolModeChange={() => undefined}
        onOrderedExcludePreviousSpeakerChange={() => undefined}
        onMaxRelayTurnsChange={() => undefined}
        onRetrySettings={() => undefined}
        onDeleteRoom={async () => undefined}
      />
    );

    const section = screen.getByRole("heading", { name: "채널 설정" }).closest("section");
    if (!section) throw new Error("Channel settings section was not rendered");
    const channelControls = within(section).getAllByRole("combobox");
    expect(channelControls).toHaveLength(1);

    await userEvent.selectOptions(channelControls[0], "mentions");
    expect(onChannelSettingChange).toHaveBeenCalledWith("lobby", {
      notifications: "mentions",
    });
  });
});
