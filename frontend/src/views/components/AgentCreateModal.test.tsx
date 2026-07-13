import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import AgentCreateModal from "./AgentCreateModal";

afterEach(cleanup);


describe("AgentCreateModal", () => {
  it("submits only a server-catalog model value selected from a dropdown", async () => {
    const onCreate = vi.fn().mockResolvedValue(undefined);
    render(
      <AgentCreateModal
        open
        meetingId="room-a"
        roomLabel="Room A"
        providers={[
          {
            id: "codex",
            display_name: "Codex",
            provider_kind: "codex_cli",
            runtime_kind: "live_cli",
            connection_kind: "native_cli_bridge",
            executable: "codex",
            default_model: "gpt-5.6-luna",
            interactive: true,
            startable: true,
            available: true,
            controls: [
              {
                key: "model",
                label: "모델",
                kind: "combobox",
                default_value: "gpt-5.6-luna",
                options: [
                  { value: "gpt-5.6-luna", label: "Luna" },
                  { value: "gpt-5.3-codex-spark", label: "Spark" },
                ],
              },
            ],
          },
        ]}
        onClose={() => undefined}
        onCreate={onCreate}
      />
    );

    const model = screen.getByRole("combobox", { name: "모델" });
    expect(model.tagName).toBe("SELECT");
    expect(screen.getByRole("option", { name: "Luna · gpt-5.6-luna" })).toBeTruthy();

    await userEvent.selectOptions(model, "gpt-5.3-codex-spark");
    await userEvent.click(screen.getByRole("button", { name: "추가하고 시작" }));

    expect(onCreate).toHaveBeenCalledWith(
      expect.objectContaining({ modelId: "gpt-5.3-codex-spark" })
    );
  });

  it("can explicitly re-add an existing stopped session", async () => {
    const onCreate = vi.fn().mockResolvedValue(undefined);
    render(
      <AgentCreateModal
        open
        meetingId="room-a"
        roomLabel="Room A"
        providers={[
          {
            id: "codex",
            display_name: "Codex",
            provider_kind: "codex_cli",
            runtime_kind: "live_cli",
            connection_kind: "native_cli_bridge",
            executable: "codex",
            default_model: "gpt-5.6-luna",
            interactive: true,
            startable: true,
            available: true,
            controls: [],
          },
        ]}
        existingSessions={[
          {
            room_id: "room-a",
            session_id: "codex-existing",
            participant_id: "codex-existing",
            display_name: "Codex Existing",
            status: "detached",
            runtime_status: "stopped",
            enabled: false,
            provider_kind: "codex_cli",
            runtime_kind: "live_cli",
            connection_kind: "native_cli_bridge",
            model: "gpt-5.6-luna",
          },
        ]}
        onClose={() => undefined}
        onCreate={onCreate}
      />
    );

    await userEvent.selectOptions(screen.getByRole("combobox", { name: "기존 세션" }), "codex-existing");
    await userEvent.click(screen.getByRole("button", { name: "추가하고 시작" }));

    expect(onCreate).toHaveBeenCalledWith(expect.objectContaining({ sessionId: "codex-existing" }));
  });
});
