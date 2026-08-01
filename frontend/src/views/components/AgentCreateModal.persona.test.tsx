import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { NativeCliProviderAvailability } from "../../roomSocketClient";
import AgentCreateModal from "./AgentCreateModal";

const api = vi.hoisted(() => ({
  chooseLocalWorkspace: vi.fn(),
  fetchProviderCredentialStatus: vi.fn(),
  fetchPersonaAssets: vi.fn(),
  importPersonaAsset: vi.fn(),
}));

vi.mock("../../api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api")>()),
  chooseLocalWorkspace: api.chooseLocalWorkspace,
  fetchProviderCredentialStatus: api.fetchProviderCredentialStatus,
}));

vi.mock("../../api/personas", () => ({
  fetchPersonaAssets: api.fetchPersonaAssets,
  importPersonaAsset: api.importPersonaAsset,
}));

afterEach(cleanup);

beforeEach(() => {
  api.chooseLocalWorkspace.mockResolvedValue({ selected: true, path: "/tmp/workspace" });
  api.fetchProviderCredentialStatus.mockResolvedValue({ configured: false, source: "missing" });
  api.fetchPersonaAssets.mockResolvedValue([
    {
      id: "night-guide",
      display_name: "Night Guide",
      asset_kind: "card",
      lorebook_count: 2,
      asset_count: 1,
      ignored_feature_count: 0,
      tag_count: 0,
    },
  ]);
});

describe("AgentCreateModal persona selection", () => {
  it("submits the bot card selected for a new API session", async () => {
    const onCreate = vi.fn().mockResolvedValue(undefined);
    render(
      <AgentCreateModal
        open
        meetingId="room-a"
        roomLabel="Room A"
        catalogRevision="cat-api"
        providers={[deepSeekProvider()]}
        onClose={() => undefined}
        onCreate={onCreate}
      />
    );

    await userEvent.click(screen.getByRole("listitem", { name: "API" }));
    await userEvent.click(screen.getByRole("listitem", { name: "DeepSeek" }));
    await userEvent.click(screen.getByRole("button", { name: /적용 안 함/ }));
    await waitFor(() => expect(screen.getByRole("radio", { name: /Night Guide/ })).toBeTruthy());
    await userEvent.click(screen.getByRole("radio", { name: /Night Guide/ }));
    await userEvent.click(screen.getByRole("button", { name: "폴더 선택" }));
    await userEvent.click(
      screen.getByRole("dialog", { name: "에이전트 추가" })
        .querySelector<HTMLButtonElement>(".dc-agent-create-primary")!
    );

    expect(onCreate).toHaveBeenCalledWith(
      expect.objectContaining({ providerId: "deepseek", personaCardId: "night-guide" })
    );
  });
});

function deepSeekProvider(): NativeCliProviderAvailability {
  return {
    id: "deepseek",
    display_name: "DeepSeek",
    provider_kind: "deepseek_api",
    runtime_kind: "api",
    connection_kind: "native_cli_bridge",
    executable: "",
    default_model: "deepseek-chat",
    interactive: true,
    startable: true,
    available: true,
    controls: [],
  };
}
