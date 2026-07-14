import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Radio } from "lucide-react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DEFAULT_ROOM_APPEARANCE } from "../../lib/roomAppearance";
import RoomSettingsModal from "./RoomSettingsModal";

afterEach(cleanup);

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
  conversationMode: "ordered" | "ambient" | "continuous",
  onConversationModeChange = vi.fn()
) {
  render(
    <RoomSettingsModal
      room={room}
      appearance={DEFAULT_ROOM_APPEARANCE}
      channelSettings={{}}
      conversationMode={conversationMode}
      maxRelayTurns={6}
      canInvite
      onClose={() => undefined}
      onInvite={() => undefined}
      onRoomChange={() => undefined}
      onAppearanceChange={() => undefined}
      onChannelSettingChange={() => undefined}
      onConversationModeChange={onConversationModeChange}
      onMaxRelayTurnsChange={() => undefined}
      onDeleteRoom={async () => undefined}
    />
  );
  return onConversationModeChange;
}

describe("RoomSettingsModal conversation mode", () => {
  it("lets the user activate ambient discussion from the room settings UI", async () => {
    const onConversationModeChange = renderSettings("ordered");

    await userEvent.click(screen.getByRole("radio", { name: /자유 토론/ }));

    expect(onConversationModeChange).toHaveBeenCalledWith("ambient");
    expect(
      screen.getByText(/사람처럼 방을 계속 지켜보는 기능이 아닙니다/)
    ).toBeTruthy();
  });

  it("shows legacy continuous mode only for a room that already uses it", () => {
    const { rerender } = render(
      <RoomSettingsModal
        room={room}
        appearance={DEFAULT_ROOM_APPEARANCE}
        channelSettings={{}}
        conversationMode="ordered"
        maxRelayTurns={6}
        canInvite
        onClose={() => undefined}
        onInvite={() => undefined}
        onRoomChange={() => undefined}
        onAppearanceChange={() => undefined}
        onChannelSettingChange={() => undefined}
        onConversationModeChange={() => undefined}
        onMaxRelayTurnsChange={() => undefined}
        onDeleteRoom={async () => undefined}
      />
    );

    expect(screen.queryByRole("radio", { name: /기존 연쇄 대화/ })).toBeNull();

    rerender(
      <RoomSettingsModal
        room={room}
        appearance={DEFAULT_ROOM_APPEARANCE}
        channelSettings={{}}
        conversationMode="continuous"
        maxRelayTurns={6}
        canInvite
        onClose={() => undefined}
        onInvite={() => undefined}
        onRoomChange={() => undefined}
        onAppearanceChange={() => undefined}
        onChannelSettingChange={() => undefined}
        onConversationModeChange={() => undefined}
        onMaxRelayTurnsChange={() => undefined}
        onDeleteRoom={async () => undefined}
      />
    );

    expect(
      (screen.getByRole("radio", { name: /기존 연쇄 대화/ }) as HTMLInputElement).checked
    ).toBe(true);
  });
});
