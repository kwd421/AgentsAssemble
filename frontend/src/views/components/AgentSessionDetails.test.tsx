import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { RoomAgentSession } from "../../api";
import type { NativeCliProviderAvailability } from "../../roomSocketClient";
import AgentSessionDetails from "./AgentSessionDetails";

const personaApi = vi.hoisted(() => ({
  fetchPersonaAssets: vi.fn(),
  importPersonaAsset: vi.fn(),
}));

vi.mock("../../api/personas", () => ({
  fetchPersonaAssets: personaApi.fetchPersonaAssets,
  importPersonaAsset: personaApi.importPersonaAsset,
}));

afterEach(cleanup);

beforeEach(() => {
  personaApi.fetchPersonaAssets.mockReset();
  personaApi.fetchPersonaAssets.mockResolvedValue([
    {
      id: "new-guide",
      display_name: "New Guide",
      asset_kind: "card",
      lorebook_count: 1,
      asset_count: 0,
      ignored_feature_count: 0,
      tag_count: 0,
    },
  ]);
});

describe("AgentSessionDetails diagnostics", () => {
  it("shows which provider permissions were denied", () => {
    const session: RoomAgentSession = {
      room_id: "room-1",
      session_id: "session-1",
      participant_id: "agent-1",
      display_name: "Agent One",
      status: "error",
      runtime_status: "error",
      enabled: true,
      provider_kind: "grok_acp",
      runtime_kind: "acp",
      connection_kind: "agent_session",
      permission_request_count: 2,
      permission_denied_count: 2,
      denied_permission_names: ["shell.execute", "files.write"],
    };

    render(<AgentSessionDetails session={session} />);
    fireEvent.click(screen.getByText("고급 진단"));

    expect(screen.getByText(/shell\.execute/)).toBeTruthy();
    expect(screen.getByText(/files\.write/)).toBeTruthy();
  });

  it("replaces the applied bot card on a stopped API session", async () => {
    const onConfigure = vi.fn().mockResolvedValue(undefined);
    const provider: NativeCliProviderAvailability = {
      id: "deepseek",
      display_name: "DeepSeek",
      provider_kind: "deepseek_api",
      runtime_kind: "api",
      connection_kind: "native_cli_bridge",
      executable: "",
      default_model: "deepseek-chat",
      catalog_group: "api",
      interactive: true,
      startable: true,
      available: true,
      controls: [],
    };
    const session: RoomAgentSession = {
      room_id: "room-1",
      session_id: "session-1",
      participant_id: "agent-1",
      display_name: "Guide",
      status: "stopped",
      runtime_status: "stopped",
      enabled: false,
      provider_kind: "deepseek_api",
      runtime_kind: "api",
      connection_kind: "agent_session",
      persona_card_id: "old-guide",
      persona_card: {
        id: "old-guide",
        display_name: "Old Guide",
        asset_kind: "card",
        lorebook_count: 1,
        asset_count: 0,
        ignored_feature_count: 0,
        tag_count: 0,
      },
    };

    render(
      <AgentSessionDetails
        session={session}
        provider={provider}
        onConfigure={onConfigure}
      />
    );

    expect(screen.getByText("현재 적용 · Old Guide")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: /Old Guide/ }));
    await waitFor(() => expect(screen.getByRole("radio", { name: /New Guide/ })).toBeTruthy());
    await userEvent.click(screen.getByRole("radio", { name: /New Guide/ }));
    await userEvent.click(screen.getByRole("button", { name: "적용 교체" }));

    expect(onConfigure).toHaveBeenCalledWith(session, {
      persona_card_id: "new-guide",
    });
  });
});
