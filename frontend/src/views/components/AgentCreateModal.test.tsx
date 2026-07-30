import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { NativeCliProviderAvailability } from "../../roomSocketClient";
import AgentCreateModal from "./AgentCreateModal";

const apiMocks = vi.hoisted(() => ({
  chooseLocalWorkspace: vi.fn(),
  deleteProviderCredential: vi.fn(),
  fetchProviderCredentialStatus: vi.fn(),
  refreshProviderCatalog: vi.fn(),
  setProviderCredential: vi.fn(),
  startFrontendLiveAgentLogin: vi.fn(),
}));

vi.mock("../../api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api")>()),
  chooseLocalWorkspace: apiMocks.chooseLocalWorkspace,
  deleteProviderCredential: apiMocks.deleteProviderCredential,
  fetchProviderCredentialStatus: apiMocks.fetchProviderCredentialStatus,
  refreshProviderCatalog: apiMocks.refreshProviderCatalog,
  setProviderCredential: apiMocks.setProviderCredential,
  startFrontendLiveAgentLogin: apiMocks.startFrontendLiveAgentLogin,
}));

afterEach(cleanup);

beforeEach(() => {
  apiMocks.chooseLocalWorkspace.mockReset();
  apiMocks.chooseLocalWorkspace.mockResolvedValue({
    selected: true,
    path: "/tmp/agentsassemble-workspace",
  });
  apiMocks.fetchProviderCredentialStatus.mockReset();
  apiMocks.fetchProviderCredentialStatus.mockResolvedValue({
    configured: false,
    source: "missing",
  });
  apiMocks.deleteProviderCredential.mockReset();
  apiMocks.refreshProviderCatalog.mockReset();
  apiMocks.refreshProviderCatalog.mockResolvedValue({
    status: "ready",
    catalog_revision: "cat-authenticated",
    providers: [],
  });
  apiMocks.setProviderCredential.mockReset();
  apiMocks.setProviderCredential.mockResolvedValue({
    configured: true,
    source: "keyring",
  });
  apiMocks.startFrontendLiveAgentLogin.mockReset();
  apiMocks.startFrontendLiveAgentLogin.mockResolvedValue({
    status: "authenticated",
    provider_id: "cursor",
    message: "Cursor 로그인이 완료됐습니다.",
  });
});

function primaryActionButton(): HTMLButtonElement {
  const button = screen
    .getByRole("dialog", { name: "에이전트 추가" })
    .querySelector<HTMLButtonElement>(".dc-agent-create-primary");
  if (!button) throw new Error("Agent create primary action was not rendered");
  return button;
}

async function chooseWorkspace(): Promise<void> {
  await userEvent.click(screen.getByRole("button", { name: "폴더 선택" }));
}

async function chooseProviderControl(label: string, option: string): Promise<void> {
  const toggle = screen.queryByRole("switch", { name: label });
  if (toggle) {
    if (!toggle.textContent?.includes(option)) {
      await userEvent.click(toggle);
    }
    if (!toggle.textContent?.includes(option)) {
      await userEvent.click(toggle);
    }
    return;
  }
  await userEvent.click(screen.getByRole("combobox", { name: label }));
  const directOption = screen.queryByRole("menuitemradio", { name: option });
  if (directOption) {
    await userEvent.click(directOption);
    return;
  }
  const family = modelFamilyFromLabel(option);
  if (label === "모델" && family) {
    const familyItem = screen.queryByRole("menuitem", { name: family });
    if (familyItem) await userEvent.click(familyItem);
  }
  await userEvent.click(screen.getByRole("option", { name: option }));
}

function modelFamilyFromLabel(label: string): string {
  return ["Haiku", "Sonnet", "Opus", "Fable", "GPT", "Gemini", "Grok", "GLM", "Kimi"]
    .find((family) => new RegExp(`(^|\\s)${family}(\\s|$)`, "i").test(label)) || "";
}

function expectProviderControlValue(label: string, option: string): void {
  expect(screen.getByLabelText(label).textContent).toContain(option);
}

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
    await userEvent.click(model);
    expect(screen.getByRole("option", { name: "Luna" })).toBeTruthy();
    await userEvent.click(model);

    await chooseProviderControl("모델", "Spark");
    expect(primaryActionButton().hasAttribute("disabled")).toBe(true);
    await chooseWorkspace();
    await userEvent.click(primaryActionButton());

    expect(onCreate).toHaveBeenCalledWith(
      expect.objectContaining({
        catalogRevision: "cat-test",
        modelId: "gpt-5.3-codex-spark",
        workspacePath: "/tmp/agentsassemble-workspace",
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
    await chooseProviderControl("기존 세션", "Codex Existing · gpt-5.6-luna");
    await userEvent.click(primaryActionButton());

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
    await userEvent.click(screen.getByRole("combobox", { name: "모델" }));
    expect(
      screen.getByRole("menuitemradio", { name: "Claude Sonnet 4.6" })
    ).toBeTruthy();
    await userEvent.click(screen.getByRole("combobox", { name: "모델" }));

    await chooseProviderControl("모델", "Claude Sonnet 4.6");
    await chooseWorkspace();
    await userEvent.click(primaryActionButton());

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
    await chooseProviderControl("모델", "Claude Sonnet 4.6");

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

    expectProviderControlValue("모델", "Claude Sonnet 4.6");
    expect(screen.getByRole("listitem", { name: "Claude Code" }).getAttribute("data-active")).toBe("true");
  });

  it("keeps a provider usable while its last verified catalog is shown", async () => {
    const onCreate = vi.fn().mockResolvedValue(undefined);
    const warning = "Catalog refresh timed out. Using the last verified model list.";
    render(
      <AgentCreateModal
        open
        meetingId="room-a"
        roomLabel="Room A"
        catalogRevision="cat-stale"
        providers={[
          {
            ...codexProvider(),
            catalog_source: "stale_cache",
            discovery_error_code: "model_discovery_timeout",
            discovery_error: warning,
          },
        ]}
        onClose={() => undefined}
        onCreate={onCreate}
      />
    );

    await userEvent.click(screen.getByRole("listitem", { name: "Codex" }));
    await chooseWorkspace();

    expect(screen.getByText(warning)).toBeTruthy();
    expect(primaryActionButton().hasAttribute("disabled")).toBe(false);
    await userEvent.click(primaryActionButton());
    expect(onCreate).toHaveBeenCalledWith(
      expect.objectContaining({
        providerId: "codex",
        catalogRevision: "cat-stale",
        modelId: "gpt-5.6-luna",
      })
    );
  });

  it("waits for browser login and does not ask the operator to recheck it manually", async () => {
    render(
      <AgentCreateModal
        open
        meetingId="room-a"
        roomLabel="Room A"
        catalogRevision="cat-auth-required"
        providers={[
          {
            ...codexProvider(),
            id: "cursor",
            display_name: "Cursor",
            provider_kind: "cursor_live_session",
            executable: "cursor-agent",
            startable: false,
            discovery_status: "failed",
            discovery_error_code: "authentication_required",
            discovery_error: "Cursor CLI 로그인이 필요합니다.",
            login_available: true,
            login_label: "Cursor 로그인",
            login_flow: "browser_oauth",
            controls: [],
          },
        ]}
        onClose={() => undefined}
        onCreate={vi.fn()}
      />
    );

    await userEvent.click(screen.getByRole("listitem", { name: "Cursor" }));
    await userEvent.click(screen.getByRole("button", { name: "Cursor 로그인" }));

    expect(apiMocks.startFrontendLiveAgentLogin).toHaveBeenCalledWith("cursor");
    expect(screen.getByText("Cursor 로그인이 완료됐습니다.")).toBeTruthy();
    expect(
      screen.queryByRole("button", { name: "로그인 완료 후 다시 확인" })
    ).toBeNull();
    expect(apiMocks.refreshProviderCatalog).not.toHaveBeenCalled();
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
    await chooseProviderControl("모델", "Claude Sonnet 4.6");

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
    expect(primaryActionButton().hasAttribute("disabled")).toBe(true);
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
    await chooseProviderControl("모델", "Claude Sonnet 4.6");
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

    expectProviderControlValue("모델", "선택 필요");
    expect(primaryActionButton().hasAttribute("disabled")).toBe(true);
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
    expectProviderControlValue("모델", "선택 필요");
    expect(primaryActionButton().hasAttribute("disabled")).toBe(true);
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
    await chooseProviderControl("모델", "Variable model");

    await userEvent.click(screen.getByRole("combobox", { name: "추론 강도" }));
    expect(screen.getByRole("option", { name: "low" })).toBeTruthy();
    expect(screen.getByRole("option", { name: "high" })).toBeTruthy();
    await userEvent.click(screen.getByRole("combobox", { name: "추론 강도" }));
    expect(screen.queryByRole("option", { name: "Fast" })).toBeNull();
    expect(
      (screen.getByRole("switch", { name: "응답 속도" }) as HTMLButtonElement).disabled
    ).toBe(true);
    expectProviderControlValue("응답 속도", "기본");

    await chooseProviderControl("추론 강도", "high");
    expect(
      (screen.getByRole("switch", { name: "응답 속도" }) as HTMLButtonElement).disabled
    ).toBe(false);
    await chooseProviderControl("응답 속도", "Fast");
  });

  it("keeps a provider option menu open while the user scrolls its options", async () => {
    render(
      <AgentCreateModal
        open
        meetingId="room-a"
        roomLabel="Room A"
        catalogRevision="cat-scroll"
        providers={[codexProviderWithRelations()]}
        onClose={() => undefined}
        onCreate={vi.fn()}
      />
    );

    await userEvent.click(screen.getByRole("listitem", { name: "Codex" }));
    await userEvent.click(screen.getByRole("combobox", { name: "모델" }));
    const menu = screen.getByRole("listbox", { name: "모델" });

    fireEvent.scroll(menu);

    expect(screen.getByRole("option", { name: "Variable model" })).toBeTruthy();
    expect(screen.getByRole("combobox", { name: "모델" }).getAttribute("aria-expanded")).toBe(
      "true"
    );
  });

  it("requires explicit dependent settings after a model or effort change", async () => {
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
    await chooseProviderControl("모델", "High model");
    expectProviderControlValue("추론 강도", "선택 필요");
    expectProviderControlValue("응답 속도", "기본");
    await chooseProviderControl("추론 강도", "high");

    await chooseProviderControl("모델", "Variable model");
    await chooseProviderControl("추론 강도", "high");
    await chooseProviderControl("응답 속도", "Fast");
    await chooseProviderControl("추론 강도", "low");
    expectProviderControlValue("응답 속도", "선택 필요");
    await userEvent.click(screen.getByRole("switch", { name: "응답 속도" }));
    expect(screen.queryByRole("option", { name: "Fast" })).toBeNull();
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

    expectProviderControlValue("추론 강도", "선택 필요");
    expect(primaryActionButton().hasAttribute("disabled")).toBe(true);
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
    expect(
      screen.getByRole("listitem", { name: "Codex" }).querySelector('[data-provider-brand="codex"]')
    ).not.toBeNull();
    expect(
      screen.getByRole("listitem", { name: "Claude Code" }).querySelector('[data-provider-brand="claude"]')
    ).not.toBeNull();
    expect(screen.getByRole("button", { name: "추가" }).hasAttribute("disabled")).toBe(true);
  });

  it("groups a long model menu by recognizable model family", async () => {
    const provider = claudeProvider();
    provider.controls[0].options.splice(1, 0, {
      value: "claude-sonnet-5",
      label: "Claude Sonnet 5",
    });
    render(
      <AgentCreateModal
        open
        meetingId="room-a"
        roomLabel="Room A"
        catalogRevision="cat-families"
        providers={[provider]}
        onClose={() => undefined}
        onCreate={vi.fn()}
      />
    );

    await userEvent.click(screen.getByRole("listitem", { name: "Claude Code" }));
    await userEvent.click(screen.getByRole("combobox", { name: "모델" }));

    expect(
      screen.getByRole("menuitemradio", { name: "Claude Haiku 4.5" })
    ).toBeTruthy();
    expect(screen.queryByRole("menuitem", { name: "Haiku" })).toBeNull();
    await userEvent.click(screen.getByRole("menuitem", { name: "Sonnet" }));
    expect(
      within(screen.getByRole("listbox", { name: "Sonnet 모델" })).getByRole(
        "option",
        { name: "Claude Sonnet 4.6" }
      )
    ).toBeTruthy();
  });

  it("uses catalog metadata to group models and expose pricing badges", async () => {
    render(
      <AgentCreateModal
        open
        meetingId="room-a"
        roomLabel="Room A"
        catalogRevision="cat-opencode"
        providers={[openCodeProvider()]}
        onClose={() => undefined}
        onCreate={vi.fn()}
      />
    );

    await userEvent.click(screen.getByRole("listitem", { name: "OpenCode" }));
    await userEvent.click(screen.getByRole("combobox", { name: "모델" }));

    await userEvent.click(screen.getByRole("menuitem", { name: "Zen" }));
    const zenModels = screen.getByRole("listbox", { name: "Zen 모델" });
    const freeModel = within(zenModels).getByRole("option", {
        name: "DeepSeek V4 Flash Free",
      });
    expect(freeModel).toBeTruthy();
    expect(within(freeModel).getByText("Free")).toBeTruthy();

    await userEvent.click(screen.getByRole("menuitem", { name: "Go" }));
    expect(
      within(screen.getByRole("listbox", { name: "Go 모델" })).getByRole(
        "option",
        { name: "GLM 5.2" }
      )
    ).toBeTruthy();
  });

  it("routes API providers through the API choice before creating the selected provider", async () => {
    const onCreate = vi.fn().mockResolvedValue(undefined);
    render(
      <AgentCreateModal
        open
        meetingId="room-a"
        roomLabel="Room A"
        catalogRevision="cat-api"
        providers={[codexProvider(), deepSeekProvider()]}
        onClose={() => undefined}
        onCreate={onCreate}
      />
    );

    expect(screen.queryByRole("listitem", { name: "DeepSeek" })).toBeNull();
    await userEvent.click(screen.getByRole("listitem", { name: "API" }));
    expect(screen.getByRole("list", { name: "API 프로바이더" })).toBeTruthy();
    expect(screen.queryByLabelText("API 키")).toBeNull();

    await userEvent.click(screen.getByRole("listitem", { name: "DeepSeek" }));
    expect(screen.getByLabelText("API 키")).toBeTruthy();
    await chooseWorkspace();
    await userEvent.click(primaryActionButton());

    expect(onCreate).toHaveBeenCalledWith(
      expect.objectContaining({
        providerId: "deepseek",
      })
    );
  });

  it("projects one mixed-location provider into matching Subscription and Local model lists", async () => {
    const onCreate = vi.fn().mockResolvedValue(undefined);
    render(
      <AgentCreateModal
        open
        meetingId="room-a"
        roomLabel="Room A"
        catalogRevision="cat-local"
        providers={[ollamaProvider(), lmStudioProvider()]}
        onClose={() => undefined}
        onCreate={onCreate}
      />
    );

    expect(screen.getByRole("listitem", { name: "Ollama" })).toBeTruthy();
    expect(screen.queryByRole("listitem", { name: "LM Studio" })).toBeNull();
    await userEvent.click(screen.getByRole("listitem", { name: "Ollama" }));
    expectProviderControlValue("모델", "Nemotron 3 Super");
    expect(screen.getByRole("combobox", { name: "모델" }).textContent).toContain(
      "Free tier"
    );

    await userEvent.click(screen.getByRole("listitem", { name: "Local" }));
    expect(screen.getByRole("listitem", { name: "Ollama" })).toBeTruthy();
    expect(screen.getByRole("listitem", { name: "LM Studio" })).toBeTruthy();
    await userEvent.click(screen.getByRole("listitem", { name: "Ollama" }));

    expect(screen.queryByLabelText("API 키")).toBeNull();
    const model = screen.getByRole("combobox", { name: "모델" }) as HTMLButtonElement;
    expect(model.disabled).toBe(true);
    expect(model.textContent).toContain("Gemma 4 12B");
    expect(
      (screen.getByRole("combobox", { name: "추론 강도" }) as HTMLButtonElement).disabled
    ).toBe(true);
    expect(
      (screen.getByRole("switch", { name: "응답 속도" }) as HTMLButtonElement).disabled
    ).toBe(true);
    expect(
      (screen.getByRole("combobox", { name: "권한" }) as HTMLButtonElement).disabled
    ).toBe(true);
    expect(screen.queryByRole("button", { name: "폴더 선택" })).toBeNull();
    await userEvent.click(primaryActionButton());

    expect(onCreate).toHaveBeenCalledWith(
      expect.objectContaining({
        providerId: "ollama",
        modelId: "gemma4:12b",
      })
    );
  });

  it("does not retain an unsaved provider secret after the modal closes", async () => {
    const provider = deepSeekProvider();
    const view = render(
      <AgentCreateModal
        open
        meetingId="room-a"
        roomLabel="Room A"
        catalogRevision="cat-secret"
        providers={[provider]}
        onClose={() => undefined}
        onCreate={vi.fn()}
      />
    );

    await userEvent.click(screen.getByRole("listitem", { name: "API" }));
    await userEvent.click(screen.getByRole("listitem", { name: "DeepSeek" }));
    const secretInput = screen.getByLabelText("API 키") as HTMLInputElement;
    await userEvent.type(secretInput, "sk-not-saved");
    expect(secretInput.value).toBe("sk-not-saved");

    view.rerender(
      <AgentCreateModal
        open={false}
        meetingId="room-a"
        roomLabel="Room A"
        catalogRevision="cat-secret"
        providers={[provider]}
        onClose={() => undefined}
        onCreate={vi.fn()}
      />
    );
    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: "에이전트 추가" })).toBeNull()
    );
    view.rerender(
      <AgentCreateModal
        open
        meetingId="room-a"
        roomLabel="Room A"
        catalogRevision="cat-secret"
        providers={[provider]}
        onClose={() => undefined}
        onCreate={vi.fn()}
      />
    );

    expect((await screen.findByLabelText("API 키") as HTMLInputElement).value).toBe("");
  });

  it("stores a Cerebras key for the API provider the operator selected", async () => {
    render(
      <AgentCreateModal
        open
        meetingId="room-a"
        roomLabel="Room A"
        catalogRevision="cat-cerebras"
        providers={[deepSeekProvider(), cerebrasProvider()]}
        onClose={() => undefined}
        onCreate={vi.fn()}
      />
    );

    await userEvent.click(screen.getByRole("listitem", { name: "API" }));
    await userEvent.click(
      screen.getByRole("listitem", { name: "Cerebras" })
    );
    await userEvent.type(screen.getByLabelText("API 키"), "csk-private");
    await userEvent.click(screen.getByRole("button", { name: "보안 저장" }));

    await waitFor(() =>
      expect(apiMocks.setProviderCredential).toHaveBeenCalledWith(
        "cerebras",
        "csk-private"
      )
    );
    expect((screen.getByLabelText("API 키") as HTMLInputElement).value).toBe("");
    expect(screen.getByText(/키 설정됨/)).toBeTruthy();
  });

  it("keeps credential deletion retryable when the secure store rejects it", async () => {
    apiMocks.fetchProviderCredentialStatus.mockResolvedValue({
      configured: true,
      source: "keyring",
    });
    apiMocks.deleteProviderCredential.mockRejectedValue(
      new Error("secure store unavailable")
    );
    render(
      <AgentCreateModal
        open
        meetingId="room-a"
        roomLabel="Room A"
        catalogRevision="cat-secret"
        providers={[deepSeekProvider()]}
        onClose={() => undefined}
        onCreate={vi.fn()}
      />
    );

    await userEvent.click(screen.getByRole("listitem", { name: "API" }));
    await userEvent.click(screen.getByRole("listitem", { name: "DeepSeek" }));
    const deleteButton = await screen.findByRole("button", { name: "저장 키 삭제" });
    await userEvent.click(deleteButton);

    await waitFor(() =>
      expect(screen.getByText("secure store unavailable")).toBeTruthy()
    );
    expect(screen.getByRole("dialog", { name: "에이전트 추가" })).toBeTruthy();
    expect((deleteButton as HTMLButtonElement).disabled).toBe(false);
    expect(screen.getByText(/키 설정됨/)).toBeTruthy();
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

function cerebrasProvider(): NativeCliProviderAvailability {
  return {
    ...deepSeekProvider(),
    id: "cerebras",
    display_name: "Cerebras",
    provider_kind: "cerebras_api",
    default_model: "gpt-oss-120b",
  };
}

function ollamaProvider(): NativeCliProviderAvailability {
  return {
    ...deepSeekProvider(),
    id: "ollama",
    display_name: "Ollama",
    provider_kind: "ollama_api",
    catalog_group: "subscription",
    workspace_required: false,
    default_model: "nemotron-3-super:cloud",
    controls: [
      {
        key: "model",
        label: "모델",
        kind: "combobox",
        default_value: "nemotron-3-super:cloud",
        options: [
          {
            value: "nemotron-3-super:cloud",
            label: "Nemotron 3 Super",
            metadata: {
              catalog_group: "subscription",
              execution_location: "cloud",
              pricing: "free_tier",
            },
          },
          {
            value: "gemma4:12b",
            label: "Gemma 4 12B",
            metadata: {
              catalog_group: "local",
              execution_location: "local",
            },
          },
        ],
      },
    ],
  };
}

function openCodeProvider(): NativeCliProviderAvailability {
  return {
    ...deepSeekProvider(),
    id: "opencode",
    display_name: "OpenCode",
    provider_kind: "opencode_server",
    runtime_kind: "opencode",
    catalog_group: "subscription",
    executable: "opencode",
    default_model: "opencode-go/glm-5.2",
    controls: [
      {
        key: "model",
        label: "모델",
        kind: "combobox",
        default_value: "opencode-go/glm-5.2",
        options: [
          {
            value: "opencode/deepseek-v4-flash-free",
            label: "DeepSeek V4 Flash",
            metadata: { group: "Zen", pricing: "free" },
          },
          {
            value: "opencode/big-pickle",
            label: "Big Pickle",
            metadata: { group: "Zen", pricing: "free" },
          },
          {
            value: "opencode-go/glm-5.2",
            label: "GLM 5.2",
            metadata: { group: "Go" },
          },
          {
            value: "opencode-go/kimi-k3",
            label: "Kimi K3",
            metadata: { group: "Go" },
          },
        ],
      },
    ],
  };
}

function lmStudioProvider(): NativeCliProviderAvailability {
  return {
    ...deepSeekProvider(),
    id: "lmstudio",
    display_name: "LM Studio",
    provider_kind: "lmstudio_api",
    catalog_group: "local",
    workspace_required: false,
    default_model: "gemma-4-e4b-it",
    controls: [
      {
        key: "model",
        label: "모델",
        kind: "combobox",
        default_value: "gemma-4-e4b-it",
        options: [{ value: "gemma-4-e4b-it", label: "Gemma 4 E4B IT" }],
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
          {
            value: "model-variable",
            label: "Variable model",
            metadata: {
              reasoning_efforts: ["low", "high"],
              service_tiers: ["fast"],
              runtime_variants: [
                { reasoning_effort: "low", service_tier: "default" },
                { reasoning_effort: "high", service_tier: "default" },
                { reasoning_effort: "high", service_tier: "fast" },
              ],
            },
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
          { value: "fast", label: "Fast" },
        ],
      },
    ],
  };
}
