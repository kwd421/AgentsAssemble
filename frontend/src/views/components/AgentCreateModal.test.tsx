import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { NativeCliProviderAvailability } from "../../roomSocketClient";
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
        catalogRevision="cat-test"
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

    await userEvent.click(screen.getByRole("listitem", { name: "Codex" }));
    const model = screen.getByRole("combobox", { name: "모델" });
    expect(model.tagName).toBe("SELECT");
    expect(screen.getByRole("option", { name: "Luna" })).toBeTruthy();

    await userEvent.selectOptions(model, "gpt-5.3-codex-spark");
    await userEvent.click(screen.getByRole("button", { name: "추가하고 시작" }));

    expect(onCreate).toHaveBeenCalledWith(
      expect.objectContaining({
        catalogRevision: "cat-test",
        modelId: "gpt-5.3-codex-spark",
      })
    );
  });

  it("can explicitly re-add an existing stopped session", async () => {
    const onCreate = vi.fn().mockResolvedValue(undefined);
    render(
      <AgentCreateModal
        open
        meetingId="room-a"
        roomLabel="Room A"
        catalogRevision="cat-test"
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
            permission_mode: "meeting_read_only",
            runtime_profile_key: "profile-test",
          },
        ]}
        onClose={() => undefined}
        onCreate={onCreate}
      />
    );

    await userEvent.click(screen.getByRole("listitem", { name: "Codex" }));
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "기존 세션" }), "codex-existing");
    await userEvent.click(screen.getByRole("button", { name: "추가하고 시작" }));

    expect(onCreate).toHaveBeenCalledWith(expect.objectContaining({ sessionId: "codex-existing" }));
  });

  it("submits Claude Sonnet 4.6 by its exact model id", async () => {
    const onCreate = vi.fn().mockResolvedValue(undefined);
    render(
      <AgentCreateModal
        open
        meetingId="room-a"
        roomLabel="Room A"
        catalogRevision="cat-claude"
        providers={[claudeProvider()]}
        onClose={() => undefined}
        onCreate={onCreate}
      />
    );

    await userEvent.click(screen.getByRole("listitem", { name: "Claude Code" }));
    const model = screen.getByRole("combobox", { name: "모델" });
    expect(screen.getByRole("option", { name: "Claude Sonnet 4.6" })).toBeTruthy();

    await userEvent.selectOptions(model, "claude-sonnet-4-6");
    await userEvent.click(screen.getByRole("button", { name: "추가하고 시작" }));

    expect(onCreate).toHaveBeenCalledWith(
      expect.objectContaining({
        providerId: "claude",
        modelId: "claude-sonnet-4-6",
      })
    );
  });

  it("preserves provider and model selection when the catalog refreshes", async () => {
    const onCreate = vi.fn().mockResolvedValue(undefined);
    const { rerender } = render(
      <AgentCreateModal
        open
        meetingId="room-a"
        roomLabel="Room A"
        catalogRevision="cat-one"
        providers={[codexProvider(), claudeProvider()]}
        onClose={() => undefined}
        onCreate={onCreate}
      />
    );

    await userEvent.click(screen.getByRole("listitem", { name: "Claude Code" }));
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "모델" }), "claude-sonnet-4-6");

    rerender(
      <AgentCreateModal
        open
        meetingId="room-a"
        roomLabel="Room A"
        catalogRevision="cat-two"
        providers={[codexProvider(), { ...claudeProvider(), discovery_status: "ready" }]}
        onClose={() => undefined}
        onCreate={onCreate}
      />
    );

    expect((screen.getByRole("combobox", { name: "모델" }) as HTMLSelectElement).value).toBe(
      "claude-sonnet-4-6"
    );
    expect(screen.getByRole("listitem", { name: "Claude Code" }).getAttribute("data-active")).toBe("true");
  });

  it("does not switch providers when the selected provider disappears during refresh", async () => {
    const onCreate = vi.fn().mockResolvedValue(undefined);
    const { rerender } = render(
      <AgentCreateModal
        open
        meetingId="room-a"
        roomLabel="Room A"
        catalogRevision="cat-one"
        providers={[codexProvider(), claudeProvider()]}
        onClose={() => undefined}
        onCreate={onCreate}
      />
    );

    await userEvent.click(screen.getByRole("listitem", { name: "Claude Code" }));
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "모델" }), "claude-sonnet-4-6");

    rerender(
      <AgentCreateModal
        open
        meetingId="room-a"
        roomLabel="Room A"
        catalogRevision="cat-two"
        providers={[codexProvider()]}
        onClose={() => undefined}
        onCreate={onCreate}
      />
    );

    expect(screen.getByText("선택한 provider가 현재 catalog에 없습니다.")).toBeTruthy();
    expect(screen.getByRole("listitem", { name: "Codex" }).getAttribute("data-active")).toBe("false");
    expect(screen.getByRole("button", { name: "추가하고 시작" }).hasAttribute("disabled")).toBe(true);
  });

  it("invalidates a selected model removed by a catalog refresh", async () => {
    const onCreate = vi.fn().mockResolvedValue(undefined);
    const { rerender } = render(
      <AgentCreateModal
        open
        meetingId="room-a"
        roomLabel="Room A"
        catalogRevision="cat-one"
        providers={[claudeProvider()]}
        onClose={() => undefined}
        onCreate={onCreate}
      />
    );

    await userEvent.click(screen.getByRole("listitem", { name: "Claude Code" }));
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "모델" }), "claude-sonnet-4-6");
    const refreshed = claudeProvider();
    refreshed.controls[0].options = refreshed.controls[0].options.filter(
      (option) => option.value !== "claude-sonnet-4-6"
    );
    rerender(
      <AgentCreateModal
        open
        meetingId="room-a"
        roomLabel="Room A"
        catalogRevision="cat-two"
        providers={[refreshed]}
        onClose={() => undefined}
        onCreate={onCreate}
      />
    );

    expect((screen.getByRole("combobox", { name: "모델" }) as HTMLSelectElement).value).toBe("");
    expect(screen.getByRole("button", { name: "추가하고 시작" }).hasAttribute("disabled")).toBe(true);
  });

  it("does not silently choose the first option when a catalog default is invalid", async () => {
    render(
      <AgentCreateModal
        open
        meetingId="room-a"
        roomLabel="Room A"
        catalogRevision="cat-invalid"
        providers={[
          {
            ...codexProvider(),
            controls: [
              {
                key: "model",
                label: "모델",
                kind: "combobox",
                default_value: "missing-model",
                options: [{ value: "available-model", label: "Available" }],
              },
            ],
          },
        ]}
        onClose={() => undefined}
        onCreate={vi.fn()}
      />
    );

    await userEvent.click(screen.getByRole("listitem", { name: "Codex" }));
    expect((screen.getByRole("combobox", { name: "모델" }) as HTMLSelectElement).value).toBe("");
    expect(screen.getByRole("button", { name: "추가하고 시작" }).hasAttribute("disabled")).toBe(true);
    expect(screen.getByText("모델의 유효한 기본값이 없어 직접 선택해야 합니다.")).toBeTruthy();
  });

  it("narrows effort and service tier options for the selected model", async () => {
    render(
      <AgentCreateModal
        open
        meetingId="room-a"
        roomLabel="Room A"
        catalogRevision="cat-related"
        providers={[codexProviderWithRelations()]}
        onClose={() => undefined}
        onCreate={vi.fn()}
      />
    );

    await userEvent.click(screen.getByRole("listitem", { name: "Codex" }));
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "모델" }), "model-high");

    expect(screen.queryByRole("option", { name: "low" })).toBeNull();
    expect(screen.getByRole("option", { name: "high" })).toBeTruthy();
    expect(screen.queryByRole("option", { name: "Fast" })).toBeNull();
    expect(screen.getByRole("option", { name: "기본" })).toBeTruthy();
  });

  it("invalidates an effort removed by a model relation change", async () => {
    const { rerender } = render(
      <AgentCreateModal
        open
        meetingId="room-a"
        roomLabel="Room A"
        catalogRevision="cat-one"
        providers={[codexProviderWithRelations()]}
        onClose={() => undefined}
        onCreate={vi.fn()}
      />
    );

    await userEvent.click(screen.getByRole("listitem", { name: "Codex" }));
    const refreshed = codexProviderWithRelations();
    refreshed.controls[0].options[0].metadata = {
      reasoning_efforts: ["high"],
      service_tiers: ["priority"],
    };
    rerender(
      <AgentCreateModal
        open
        meetingId="room-a"
        roomLabel="Room A"
        catalogRevision="cat-two"
        providers={[refreshed]}
        onClose={() => undefined}
        onCreate={vi.fn()}
      />
    );

    expect((screen.getByRole("combobox", { name: "추론 강도" }) as HTMLSelectElement).value).toBe("");
    expect(screen.getByRole("button", { name: "추가하고 시작" }).hasAttribute("disabled")).toBe(true);
  });

  it("requires an explicit provider selection when the modal opens", () => {
    render(
      <AgentCreateModal
        open
        meetingId="room-a"
        roomLabel="Room A"
        catalogRevision="cat-explicit"
        providers={[codexProvider(), claudeProvider()]}
        onClose={() => undefined}
        onCreate={vi.fn()}
      />
    );

    expect(screen.getByText("사용할 provider를 선택하세요.")).toBeTruthy();
    expect(screen.queryByRole("combobox", { name: "모델" })).toBeNull();
    expect(screen.getByRole("listitem", { name: "Codex" }).getAttribute("data-active")).toBe("false");
    expect(screen.getByRole("listitem", { name: "Claude Code" }).getAttribute("data-active")).toBe("false");
    expect(screen.getByRole("button", { name: "추가" }).hasAttribute("disabled")).toBe(true);
  });
});

function codexProvider(): NativeCliProviderAvailability {
  return {
    id: "codex",
    display_name: "Codex",
    provider_kind: "codex_cli",
    runtime_kind: "live_cli" as const,
    connection_kind: "native_cli_bridge" as const,
    executable: "codex",
    default_model: "gpt-5.6-luna",
    interactive: true as const,
    startable: true,
    available: true,
    controls: [
      {
        key: "model",
        label: "모델",
        kind: "combobox" as const,
        default_value: "gpt-5.6-luna",
        options: [{ value: "gpt-5.6-luna", label: "Luna" }],
      },
    ],
  };
}

function claudeProvider(): NativeCliProviderAvailability {
  return {
    id: "claude",
    display_name: "Claude Code",
    provider_kind: "claude_code",
    runtime_kind: "live_cli" as const,
    connection_kind: "native_cli_bridge" as const,
    executable: "claude",
    default_model: "claude-haiku-4-5",
    interactive: true as const,
    startable: true,
    available: true,
    catalog_source: "static_manifest" as const,
    controls: [
      {
        key: "model",
        label: "모델",
        kind: "combobox" as const,
        default_value: "claude-haiku-4-5",
        options: [
          { value: "claude-haiku-4-5", label: "Claude Haiku 4.5" },
          { value: "claude-sonnet-4-6", label: "Claude Sonnet 4.6" },
        ],
      },
    ],
  };
}

function codexProviderWithRelations(): NativeCliProviderAvailability {
  return {
    ...codexProvider(),
    default_model: "model-low",
    controls: [
      {
        key: "model",
        label: "모델",
        kind: "combobox" as const,
        default_value: "model-low",
        options: [
          {
            value: "model-low",
            label: "Low model",
            metadata: { reasoning_efforts: ["low"], service_tiers: ["priority"] },
          },
          {
            value: "model-high",
            label: "High model",
            metadata: { reasoning_efforts: ["high"], service_tiers: [] },
          },
        ],
      },
      {
        key: "reasoning_effort",
        label: "추론 강도",
        kind: "select" as const,
        default_value: "low",
        options: [
          { value: "low", label: "low" },
          { value: "high", label: "high" },
        ],
      },
      {
        key: "service_tier",
        label: "응답 속도",
        kind: "select" as const,
        default_value: "default",
        options: [
          { value: "default", label: "기본" },
          { value: "priority", label: "Fast" },
        ],
      },
    ],
  };
}
