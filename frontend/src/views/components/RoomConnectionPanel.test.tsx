import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { LiveAgent, RoomAgentSession, RoomMember } from "../../api";
import type { NativeCliProviderAvailability } from "../../roomSocketClient";
import RoomConnectionPanel from "./RoomConnectionPanel";

afterEach(cleanup);

const room = {
  id: "general",
  label: "general",
  meetingId: "general",
  topic: "",
  tone: "default",
};

function agentSession(status: string): RoomAgentSession {
  return {
    room_id: "general",
    session_id: "session-codex",
    participant_id: "codex",
    display_name: "Codex Spark",
    status,
    runtime_status: status,
    enabled: true,
    provider_kind: "codex_live_session",
    runtime_kind: "live_cli",
    connection_kind: "native_cli_bridge",
  };
}

function agent(status = "online"): LiveAgent {
  return {
    agent_id: "codex",
    display_name: "Codex Spark",
    owner_id: "operator-local",
    status,
    provider_kind: "codex_live_session",
    connection_kind: "native_cli_bridge",
    engagement_mode: "agent_session",
    meeting_id: "general",
    model_id: "gpt-5.3-codex-spark",
    last_seen_at: "2026-07-11T00:00:00Z",
    last_reply_at: "2026-07-11T00:00:00Z",
    sandbox_enforcement: "read-only",
    capabilities: [],
  };
}

function member(status = "attached"): RoomMember {
  return {
    meeting_id: "general",
    participant_id: "codex",
    display_name: "Codex Spark",
    role: "agent",
    participant_type: "subscription_ai",
    provider_kind: "codex_live_session",
    connection_kind: "native_cli_bridge",
    owner_id: "operator-local",
    status,
    source: "agent_session",
    created_at: "2026-07-11T00:00:00Z",
    updated_at: "2026-07-11T00:00:00Z",
  };
}

const agentControlCapability = { "agent.control": true };

function codexProvider(): NativeCliProviderAvailability {
  return {
    id: "codex",
    display_name: "Codex",
    provider_kind: "codex_live_session",
    runtime_kind: "live_cli",
    connection_kind: "native_cli_bridge",
    executable: "codex",
    default_model: "gpt-current",
    interactive: true,
    startable: true,
    available: true,
    controls: [
      {
        key: "model",
        label: "모델",
        kind: "combobox",
        default_value: "gpt-current",
        options: [
          {
            value: "gpt-current",
            label: "Current",
            metadata: {
              reasoning_efforts: ["low"],
              runtime_variants: [
                { reasoning_effort: "low", service_tier: "default" },
              ],
            },
          },
          {
            value: "gpt-next",
            label: "Next",
            metadata: {
              reasoning_efforts: ["high"],
              runtime_variants: [
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
        kind: "select",
        default_value: "low",
        options: [
          { value: "low", label: "low" },
          { value: "high", label: "high" },
        ],
      },
      {
        key: "service_tier",
        label: "응답 속도",
        kind: "select",
        default_value: "default",
        options: [
          { value: "default", label: "기본" },
          { value: "fast", label: "Fast" },
        ],
      },
      {
        key: "permission_mode",
        label: "권한",
        kind: "select",
        default_value: "meeting_read_only",
        options: [
          { value: "meeting_read_only", label: "읽기 전용" },
          { value: "workspace_write", label: "작업 폴더 쓰기" },
        ],
      },
      {
        key: "max_output_tokens",
        label: "최대 응답 길이",
        kind: "select",
        default_value: "4096",
        options: [
          { value: "4096", label: "4,096 토큰" },
          { value: "8192", label: "8,192 토큰" },
        ],
      },
    ],
  };
}

function openAgentDetails() {
  fireEvent.click(screen.getByText("Codex Spark"));
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
  await userEvent.click(screen.getByRole("option", { name: option }));
}

function expectProviderControlValue(label: string, option: string): void {
  expect(screen.getByLabelText(label).textContent).toContain(option);
}

describe("RoomConnectionPanel", () => {
  it("does not render a separate fixed Agent Session section", () => {
    render(<RoomConnectionPanel room={room} agents={[]} members={[]} agentSessions={[]} />);

    expect(screen.queryByText("연결된 세션 없음")).toBeNull();
    expect(screen.queryByRole("region", { name: "Agent Session" })).toBeNull();
    expect(screen.queryByText("Mafia Night")).toBeNull();
    expect(screen.queryByLabelText("대화 방식")).toBeNull();
    expect(screen.queryByText("다음 턴 호출")).toBeNull();
    expect(screen.queryByRole("textbox", { name: /Agent Session/i })).toBeNull();
  });

  it("uses the canonical viewer member as the single human self row", () => {
    const viewerMember: RoomMember = {
      meeting_id: "general",
      participant_id: "operator-local",
      display_name: "호스트",
      role: "human",
      participant_type: "human",
      provider_kind: "",
      connection_kind: "agent_session",
      status: "joined",
      source: "agent_session",
      created_at: "2026-07-11T00:00:00Z",
      updated_at: "2026-07-11T00:00:00Z",
    };

    render(
      <RoomConnectionPanel
        room={room}
        agents={[]}
        members={[viewerMember]}
        viewerParticipantId="operator-local"
      />
    );

    expect(screen.getByText("호스트")).toBeTruthy();
    expect(screen.getByText("참여 중")).toBeTruthy();
    expect(screen.queryByText("나's 호스트")).toBeNull();
  });

  it("renders a canonical session once in the agent roster and opens its controls", () => {
    const onAgentControl = vi.fn();
    const session = agentSession("stopped");
    render(
      <RoomConnectionPanel
        room={room}
        agents={[agent("offline")]}
        members={[member("stopped")]}
        agentSessions={[session]}
        capabilities={agentControlCapability}
        onAgentControl={onAgentControl}
      />
    );

    expect(screen.getAllByText("Codex Spark")).toHaveLength(1);
    expect(screen.queryByTitle("세션 시작")).toBeNull();
    openAgentDetails();
    fireEvent.click(screen.getByTitle("세션 시작"));
    expect(onAgentControl).toHaveBeenCalledWith(session, "start");
    expect((screen.getByTitle("세션 중지") as HTMLButtonElement).disabled).toBe(true);
  });

  it("loads provider usage only after the owner opens that agent's details", async () => {
    const session = agentSession("idle");
    const onAgentUsageRequest = vi.fn().mockResolvedValue(undefined);
    render(
      <RoomConnectionPanel
        room={room}
        agents={[agent()]}
        members={[member()]}
        agentSessions={[session]}
        quotaViewer={{
          hostCanViewLocalAgentQuotas: true,
          localProcessAgentIds: ["codex"],
        }}
        onAgentUsageRequest={onAgentUsageRequest}
      />
    );

    expect(onAgentUsageRequest).not.toHaveBeenCalled();
    openAgentDetails();

    await waitFor(() => {
      expect(onAgentUsageRequest).toHaveBeenCalledTimes(1);
      expect(onAgentUsageRequest).toHaveBeenCalledWith(session);
    });
  });

  it("pauses an idle session and resumes a paused session", async () => {
    const onAgentControl = vi.fn().mockResolvedValue(undefined);
    const idle = agentSession("idle");
    const { getByText, getByTitle, rerender } = render(
      <RoomConnectionPanel
        room={room}
        agents={[agent()]}
        members={[member()]}
        agentSessions={[idle]}
        capabilities={agentControlCapability}
        onAgentControl={onAgentControl}
      />
    );

    openAgentDetails();
    fireEvent.click(getByTitle("세션 일시정지"));
    await waitFor(() => expect(onAgentControl).toHaveBeenCalledWith(idle, "pause"));
    expect((getByTitle("세션 재개") as HTMLButtonElement).disabled).toBe(true);

    const paused = agentSession("paused");
    rerender(
      <RoomConnectionPanel
        room={room}
        agents={[agent()]}
        members={[member()]}
        agentSessions={[paused]}
        capabilities={agentControlCapability}
        onAgentControl={onAgentControl}
      />
    );
    expect(getByText("일시정지", { selector: ".dc-member-status-chip" })).toBeTruthy();
    await waitFor(() =>
      expect((getByTitle("세션 재개") as HTMLButtonElement).disabled).toBe(false)
    );
    fireEvent.click(getByTitle("세션 재개"));
    await waitFor(() => expect(onAgentControl).toHaveBeenCalledWith(paused, "resume"));
    expect((getByTitle("세션 일시정지") as HTMLButtonElement).disabled).toBe(true);
  });

  it("does not offer process resume after an external session is stopped", () => {
    const stopped = {
      ...agentSession("stopped"),
      external_owned: true,
      started_at: "2026-07-13T00:00:00Z",
    };
    render(
      <RoomConnectionPanel
        room={room}
        agents={[agent("offline")]}
        members={[member("detached")]}
        agentSessions={[stopped]}
        capabilities={agentControlCapability}
        onAgentControl={vi.fn()}
      />
    );

    openAgentDetails();

    expect((screen.getByTitle("세션 재개") as HTMLButtonElement).disabled).toBe(true);
  });

  it("shows bounded runtime diagnostics without provider ids or raw stderr", () => {
    const session = {
      ...agentSession("idle"),
      transport: "acp_stdio",
      runtime_profile_key: "profile-4c21",
      message_source: "grok_acp",
      message_source_strict: true,
      provider_visible_chars: 418,
      provider_visible_event_count: 3,
      stderr_byte_count: 65540,
      stderr_warning_count: 17,
      notification_drop_count: 2,
      adapter_activity_invalid_count: 3,
      provider_session_active: true,
      provider_session_reused: true,
      provider_session_id: "must-not-render",
      stderr_tail: "secret terminal warning",
    } as RoomAgentSession & { provider_session_id: string; stderr_tail: string };

    render(
      <RoomConnectionPanel
        room={room}
        agents={[agent()]}
        members={[member()]}
        agentSessions={[session]}
      />
    );

    openAgentDetails();

    expect(screen.getByText("profile profile-4c21")).toBeTruthy();
    expect(screen.getByText("message grok_acp · strict")).toBeTruthy();
    expect(screen.getByText("input 418 chars · 3 events")).toBeTruthy();
    expect(screen.getByText("stderr 65540 bytes · warnings 17")).toBeTruthy();
    expect(screen.getByText("protocol drops 2")).toBeTruthy();
    expect(screen.getByText("invalid activity reports 3")).toBeTruthy();
    expect(screen.getByText("provider session 이어짐")).toBeTruthy();
    expect(screen.queryByText("must-not-render")).toBeNull();
    expect(screen.queryByText("secret terminal warning")).toBeNull();
  });

  it("does not claim that PTY providers lack a provider session", () => {
    render(
      <RoomConnectionPanel
        room={room}
        agents={[agent()]}
        members={[member()]}
        agentSessions={[{ ...agentSession("idle"), transport: "pty", provider_session_active: false }]}
      />
    );

    openAgentDetails();
    expect(screen.queryByText("provider session 비활성")).toBeNull();
    expect(screen.queryByText("provider session 재개 대기")).toBeNull();
  });

  it("changes only the viewer's thought and tool activity visibility", () => {
    const session = agentSession("idle");
    const onVisibilityChange = vi.fn();
    render(
      <RoomConnectionPanel
        room={room}
        agents={[agent()]}
        members={[member()]}
        agentSessions={[session]}
        agentActivityVisibility={{ codex: true }}
        onAgentActivityVisibilityChange={onVisibilityChange}
      />
    );

    openAgentDetails();
    const toggle = screen.getByRole("switch", { name: "생각과 작업 표시" });
    expect(toggle.getAttribute("aria-checked")).toBe("true");
    fireEvent.click(toggle);

    expect(onVisibilityChange).toHaveBeenCalledWith(session, false);
    expect(screen.getByText("공개용 생각 요약과 안전하게 정리된 도구 활동만 표시합니다.")).toBeTruthy();
  });

  it("does not expose an interactive thought-visibility control without an owner callback", () => {
    render(
      <RoomConnectionPanel
        room={room}
        agents={[agent()]}
        members={[member()]}
        agentSessions={[agentSession("idle")]}
      />
    );

    openAgentDetails();

    expect(
      (screen.getByRole("switch", { name: "생각과 작업 표시" }) as HTMLButtonElement).disabled
    ).toBe(true);
    expect(
      screen.getByRole("switch", { name: "생각과 작업 표시" }).getAttribute("aria-checked")
    ).toBe("false");
  });

  it("keeps canonical runtime controls locked while the session is running without a duplicate options card", async () => {
    const session = {
      ...agentSession("idle"),
      model: "gpt-current",
      reasoning_effort: "low",
      service_tier: "default",
      permission_mode: "meeting_read_only",
    };
    render(
      <RoomConnectionPanel
        room={room}
        agents={[agent()]}
        members={[member()]}
        agentSessions={[session]}
        capabilities={agentControlCapability}
        availableProviders={[codexProvider()]}
        onAgentConfigure={vi.fn()}
      />
    );

    openAgentDetails();

    await waitFor(() =>
      expectProviderControlValue("모델", "Current")
    );
    expect((screen.getByRole("combobox", { name: "모델" }) as HTMLButtonElement).disabled).toBe(true);
    expect(
      (screen.getByRole("combobox", { name: "추론 강도" }) as HTMLButtonElement).disabled
    ).toBe(true);
    expect((screen.getByRole("combobox", { name: "권한" }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "런타임 설정 저장" }) as HTMLButtonElement).disabled).toBe(
      true
    );
    expect(
      screen.getByText(
        "현재 세션이 실행 중이라 시작 프로필을 표시하고 있습니다. 변경하려면 세션을 중지하세요."
      )
    ).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "권한 / 속도" })).toBeNull();
  });

  it("saves all provider runtime controls together for a stopped canonical session", async () => {
    const onAgentConfigure = vi.fn().mockResolvedValue(undefined);
    const session = {
      ...agentSession("stopped"),
      enabled: false,
      model: "gpt-current",
      reasoning_effort: "low",
      service_tier: "default",
      variant: "",
      permission_mode: "meeting_read_only",
      max_output_tokens: 4096,
    };
    render(
      <RoomConnectionPanel
        room={room}
        agents={[agent("offline")]}
        members={[member("stopped")]}
        agentSessions={[session]}
        capabilities={agentControlCapability}
        availableProviders={[codexProvider()]}
        onAgentConfigure={onAgentConfigure}
      />
    );

    openAgentDetails();

    await waitFor(() =>
      expect((screen.getByRole("combobox", { name: "모델" }) as HTMLButtonElement).disabled).toBe(false)
    );
    await chooseProviderControl("모델", "Next");
    expectProviderControlValue("추론 강도", "선택 필요");
    expect(screen.queryByRole("option", { name: "low" })).toBeNull();
    await chooseProviderControl("추론 강도", "high");
    await chooseProviderControl("응답 속도", "Fast");
    await chooseProviderControl("권한", "작업 폴더 쓰기");
    await chooseProviderControl("최대 응답 길이", "8,192 토큰");
    fireEvent.click(screen.getByRole("button", { name: "런타임 설정 저장" }));

    await waitFor(() =>
      expect(onAgentConfigure).toHaveBeenCalledWith(session, {
        model: "gpt-next",
        reasoning_effort: "high",
        service_tier: "fast",
        variant: "",
        execution_harness: "builtin",
        permission_mode: "workspace_write",
        max_output_tokens: "8192",
      })
    );
    expect(screen.queryByRole("heading", { name: "권한 / 속도" })).toBeNull();
  });

  it("shows canonical runtime-setting changes received for the open session", async () => {
    const provider = codexProvider();
    const initialSession = {
      ...agentSession("stopped"),
      runtime_profile_key: "profile-test",
      model: "gpt-current",
      reasoning_effort: "low",
      service_tier: "default",
      permission_mode: "meeting_read_only",
    };
    const view = render(
      <RoomConnectionPanel
        room={room}
        agents={[agent("offline")]}
        members={[member("stopped")]}
        agentSessions={[initialSession]}
        capabilities={agentControlCapability}
        availableProviders={[provider]}
        onAgentConfigure={vi.fn()}
      />
    );

    openAgentDetails();
    await waitFor(() =>
      expectProviderControlValue("모델", "Current")
    );

    view.rerender(
      <RoomConnectionPanel
        room={room}
        agents={[agent("offline")]}
        members={[member("stopped")]}
        agentSessions={[
          {
            ...initialSession,
            model: "gpt-next",
            reasoning_effort: "high",
            service_tier: "fast",
            permission_mode: "workspace_write",
          },
        ]}
        capabilities={agentControlCapability}
        availableProviders={[provider]}
        onAgentConfigure={vi.fn()}
      />
    );

    await waitFor(() =>
      expectProviderControlValue("모델", "Next")
    );
    expectProviderControlValue("추론 강도", "high");
    expectProviderControlValue("응답 속도", "Fast");
    expectProviderControlValue("권한", "작업 폴더 쓰기");
  });

  it("does not save a stopped runtime profile that conflicts with the current catalog", async () => {
    const session = {
      ...agentSession("stopped"),
      enabled: false,
      model: "gpt-next",
      reasoning_effort: "low",
      service_tier: "default",
      permission_mode: "meeting_read_only",
    };
    render(
      <RoomConnectionPanel
        room={room}
        agents={[agent("offline")]}
        members={[member("stopped")]}
        agentSessions={[session]}
        capabilities={agentControlCapability}
        availableProviders={[codexProvider()]}
        onAgentConfigure={vi.fn()}
      />
    );

    openAgentDetails();

    await waitFor(() =>
      expect(
        (screen.getByRole("button", { name: "런타임 설정 저장" }) as HTMLButtonElement).disabled
      ).toBe(true)
    );
    expect(
      screen.getByText(/현재 선택 가능한 추론 강도 목록에 없습니다/)
    ).toBeTruthy();
  });

  it("recognizes a stored model display name as the current canonical model", async () => {
    const provider = codexProvider();
    provider.controls[0].options = [
      {
        value: "claude-opus-4-6-thinking",
        label: "Claude Opus 4.6 Thinking",
        metadata: {
          reasoning_efforts: [],
          runtime_variants: [
            { reasoning_effort: "default", service_tier: "default" },
          ],
        },
      },
    ];
    provider.controls[1].options = [{ value: "", label: "기본" }];
    const session = {
      ...agentSession("error"),
      enabled: false,
      model: "Claude Opus 4.6 (Thinking)",
      reasoning_effort: "",
      service_tier: "default",
      permission_mode: "meeting_read_only",
    };
    render(
      <RoomConnectionPanel
        room={room}
        agents={[agent("offline")]}
        members={[member("stopped")]}
        agentSessions={[session]}
        capabilities={agentControlCapability}
        availableProviders={[provider]}
        onAgentConfigure={vi.fn()}
      />
    );

    openAgentDetails();

    await waitFor(() =>
      expectProviderControlValue("모델", "Claude Opus 4.6 Thinking")
    );
    expect(
      (screen.getByRole("button", { name: "런타임 설정 저장" }) as HTMLButtonElement)
        .disabled
    ).toBe(false);
    expect(screen.queryByText(/현재 선택 가능한 모델 목록에 없습니다/)).toBeNull();
  });

  it("requires a failed bridge to be stopped before changing its runtime profile", async () => {
    const session = {
      ...agentSession("error"),
      enabled: true,
      model: "gpt-current",
      reasoning_effort: "low",
      service_tier: "default",
      permission_mode: "meeting_read_only",
    };
    render(
      <RoomConnectionPanel
        room={room}
        agents={[agent("offline")]}
        members={[member("stopped")]}
        agentSessions={[session]}
        capabilities={agentControlCapability}
        availableProviders={[codexProvider()]}
        onAgentConfigure={vi.fn()}
      />
    );

    openAgentDetails();

    await waitFor(() =>
      expect(
        (screen.getByRole("button", { name: "런타임 설정 저장" }) as HTMLButtonElement)
          .disabled
      ).toBe(true)
    );
    expect(screen.getByText(/변경하려면 세션을 중지하세요/)).toBeTruthy();
  });

});
