import { useEffect, useState } from "react";
import { CirclePause, Play, RotateCcw, Save, Square, Zap } from "lucide-react";
import type { RoomAgentSession } from "../../api";
import type { NativeCliProviderAvailability } from "../../roomSocketClient";
import {
  canonicalProviderModelValue,
  displayProviderControls,
  effectiveProviderControlOptions,
  reconcileProviderSettings,
} from "../../lib/providerControlSettings";
import AgentSessionPersonaSettings from "./AgentSessionPersonaSettings";
import AgentActivitySettings from "./AgentActivitySettings";
import ProviderRuntimeSettingField from "./ProviderRuntimeSettingField";
import WorkspacePickerField from "./WorkspacePickerField";

export type AgentSessionControlAction = "start" | "pause" | "stop" | "resume" | "interrupt";

export function agentSessionStatusLabel(status?: string) {
  if (status === "busy") return "응답 중";
  if (status === "starting") return "시작 중";
  if (status === "idle") return "대기";
  if (status === "paused") return "일시정지";
  if (status === "stopping") return "중지 중";
  if (status === "stopped") return "중지됨";
  if (status === "available") return "시작 대기";
  if (status === "error") return "오류";
  if (status === "disconnected") return "연결 끊김";
  return status || "상태 미정";
}

export function agentSessionIsPresent(status?: string) {
  return ["starting", "idle", "busy", "paused", "stopping"].includes(status || "");
}

export function agentSessionPresenceStatus(status?: string) {
  if (status === "busy" || status === "starting" || status === "stopping") return "working";
  if (status === "idle") return "online";
  if (status === "paused" || status === "available") return "idle";
  if (status === "error") return "error";
  return "offline";
}

function latencySummary(session: RoomAgentSession) {
  const latency = session.latency || {};
  const first =
    typeof latency.ttfo_ms === "number" ? `${Math.round(latency.ttfo_ms)}ms first output` : "";
  const total =
    typeof latency.total_turn_ms === "number" ? `${Math.round(latency.total_turn_ms)}ms total` : "";
  return [first, total].filter(Boolean).join(" · ");
}

function providerSessionContinuity(session: RoomAgentSession) {
  const structuredSession =
    session.transport === "acp_stdio" ||
    session.provider_session_load_supported ||
    session.provider_session_reused ||
    session.provider_session_resume_failed;
  if (!structuredSession) return "";
  if (session.provider_session_resume_failed) return "provider session 재개 실패";
  if (!session.provider_session_active && session.provider_session_load_supported) {
    return "provider session 재개 대기";
  }
  if (!session.provider_session_active) return "provider session 비활성";
  if (session.provider_session_reused) return "provider session 이어짐";
  return "provider session 활성";
}

function actionCompletedLabel(action: AgentSessionControlAction) {
  if (action === "start") return "세션 시작 요청 완료";
  if (action === "pause") return "세션 일시정지 완료";
  if (action === "stop") return "세션 중지 요청 완료";
  if (action === "resume") return "세션 재개 요청 완료";
  return "현재 응답 중단 요청 완료";
}

function sessionErrorMessage(session: RoomAgentSession) {
  if (session.last_error_code === "quota_exhausted") {
    return "Provider 할당량 또는 사용 가능 잔액이 소진되었습니다.";
  }
  if (session.last_error_code === "provider_rate_limited") {
    return "Provider 요청 속도 제한에 걸렸습니다. 할당량 소진으로 단정할 수는 없습니다.";
  }
  if (session.last_error_code === "profile_migration_required") {
    return "저장된 실행 프로필이 현재 provider 설정 형식과 달라 재사용할 수 없습니다. 아래 실행 설정을 확인한 뒤 다시 저장하세요.";
  }
  return session.last_error || "";
}

export default function AgentSessionDetails({
  session,
  provider,
  onControl,
  onConfigure,
  activityVisible = false,
  onActivityVisibilityChange,
}: {
  session: RoomAgentSession;
  provider?: NativeCliProviderAvailability;
  onControl?: (
    session: RoomAgentSession,
    action: AgentSessionControlAction
  ) => void | Promise<void>;
  onConfigure?: (
    session: RoomAgentSession,
    settings: Record<string, string>
  ) => void | Promise<void>;
  activityVisible?: boolean;
  onActivityVisibilityChange?: (session: RoomAgentSession, visible: boolean) => void;
}) {
  const [pendingAction, setPendingAction] = useState<AgentSessionControlAction | null>(null);
  const [actionStatus, setActionStatus] = useState("");
  const [settings, setSettings] = useState<Record<string, string>>({});
  const [settingsBusy, setSettingsBusy] = useState(false);
  const [workspacePath, setWorkspacePath] = useState("");
  const status = session.runtime_status;
  const hasRunBefore = Boolean(
    session.started_at ||
      session.turn_count ||
      session.last_seen_event_id
  );
  const canStart =
    !hasRunBefore && ["", "available", "stopped", "error", "disconnected"].includes(status || "");
  const canPause = status === "idle";
  const canStop = agentSessionIsPresent(status) || status === "error";
  const canResume =
    status === "paused" ||
    (!session.external_owned &&
      hasRunBefore &&
      ["stopped", "error", "disconnected", "available"].includes(status || ""));
  const canInterrupt = status === "busy";
  const continuity = providerSessionContinuity(session);
  const canConfigure =
    !session.enabled &&
    ["", "available", "stopped", "error", "disconnected"].includes(status || "");
  const runtimeSettingLabels =
    (provider?.controls || []).map((control) => control.label).join("·") || "런타임 설정";
  const invalidRuntimeControl = provider?.controls.find((control) =>
    !effectiveProviderControlOptions(provider, control, settings).some(
      (option) => option.value === (settings[control.key] ?? "")
    )
  );
  const invalidRuntimeValue = invalidRuntimeControl
    ? settings[invalidRuntimeControl.key] || ""
    : "";
  const visibleSessionError = sessionErrorMessage(session);
  const workHarnessNeedsWorkspace = Boolean(
    (
      provider?.work_harness_available &&
      settings.permission_mode === "workspace_write" &&
      session.permission_mode !== "workspace_write"
    ) || (
      settings.execution_harness &&
      settings.execution_harness !== "builtin" &&
      (session.execution_harness || "builtin") === "builtin"
    )
  );

  useEffect(() => {
    const storedModel = session.model || controlDefault(provider, "model");
    const storedSettings = {
      model:
        provider && storedModel
          ? canonicalProviderModelValue(provider, storedModel)
          : storedModel,
      reasoning_effort:
        session.reasoning_effort ?? controlDefault(provider, "reasoning_effort"),
      service_tier:
        session.service_tier ?? controlDefault(provider, "service_tier"),
      variant: session.variant ?? controlDefault(provider, "variant"),
      execution_harness:
        session.execution_harness || controlDefault(provider, "execution_harness") || "builtin",
      permission_mode:
        session.permission_mode || controlDefault(provider, "permission_mode") || "meeting_read_only",
      max_output_tokens:
        String(session.max_output_tokens || "") ||
        controlDefault(provider, "max_output_tokens"),
    };
    setSettings(storedSettings);
    setWorkspacePath("");
  }, [
    provider,
    session.session_id,
    session.runtime_profile_key,
    session.model,
    session.reasoning_effort,
    session.service_tier,
    session.variant,
    session.execution_harness,
    session.permission_mode,
    session.max_output_tokens,
  ]);

  async function runControl(action: AgentSessionControlAction) {
    if (!onControl || pendingAction) return;
    setPendingAction(action);
    setActionStatus("");
    try {
      await onControl(session, action);
      setActionStatus(actionCompletedLabel(action));
    } catch (error) {
      setActionStatus(error instanceof Error ? error.message : "세션 제어 요청 실패");
    } finally {
      setPendingAction(null);
    }
  }

  async function saveSettings() {
    if (
      !onConfigure ||
      !canConfigure ||
      invalidRuntimeControl ||
      settingsBusy ||
      (workHarnessNeedsWorkspace && !workspacePath)
    ) return;
    setSettingsBusy(true);
    setActionStatus("");
    try {
      await onConfigure(session, {
        ...settings,
        ...(workspacePath ? { workspace: workspacePath } : {}),
      });
      setActionStatus("런타임 설정 저장 완료 · 다음 시작부터 적용");
    } catch (error) {
      setActionStatus(error instanceof Error ? error.message : "런타임 설정 저장 실패");
    } finally {
      setSettingsBusy(false);
    }
  }

  function updateRuntimeSetting(key: string, value: string) {
    if (!provider) return;
    setSettings((previous) => ({
      ...previous,
      ...reconcileProviderSettings(
        provider,
        {
          ...previous,
          [key]: value,
        },
        key
      ),
    }));
  }

  return (
    <section className="dc-member-detail-section" aria-label={`${session.display_name} 실행 및 설정`}>
      <div className="dc-member-detail-section-heading">
        <h3>실행 및 설정</h3>
        <span className="dc-agent-session-state" data-state={agentSessionPresenceStatus(status)}>
          {agentSessionStatusLabel(status)}
        </span>
      </div>
      {status === "error" && visibleSessionError && (
        <p className="dc-room-play-error preserve-words">
          오류 원인 · {visibleSessionError}
        </p>
      )}
      {provider && onConfigure && (
        <div className="dc-agent-runtime-settings" aria-label={`${session.display_name} 런타임 설정`}>
          {displayProviderControls(provider).map((control) => {
            const providerSupportsControl = provider.controls.some(
              (candidate) => candidate.key === control.key
            );
            const options = providerSupportsControl
              ? effectiveProviderControlOptions(provider, control, settings)
              : control.options;
            return (
              <ProviderRuntimeSettingField
                key={`${session.session_id}:${control.key}`}
                control={control}
                options={options}
                value={
                  providerSupportsControl
                    ? settings[control.key] ?? control.default_value
                    : control.default_value
                }
                disabled={!canConfigure || settingsBusy || !providerSupportsControl}
                onChange={(value) => updateRuntimeSetting(control.key, value)}
              />
            );
          })}
          {workHarnessNeedsWorkspace && (
            <WorkspacePickerField
              value={workspacePath}
              disabled={!canConfigure || settingsBusy}
              onChange={setWorkspacePath}
              onError={setActionStatus}
            />
          )}
          <button
            type="button"
            className="dc-member-session-button"
            disabled={
              !canConfigure ||
              Boolean(invalidRuntimeControl) ||
              settingsBusy ||
              (workHarnessNeedsWorkspace && !workspacePath)
            }
            onClick={() => void saveSettings()}
          >
            <Save size={14} />
            런타임 설정 저장
          </button>
          <p className="preserve-words">
            {!canConfigure
              ? "현재 세션이 실행 중이라 시작 프로필을 표시하고 있습니다. 변경하려면 세션을 중지하세요."
              : invalidRuntimeControl
                ? invalidRuntimeValue
                  ? `저장값: “${invalidRuntimeValue}”. 현재 선택 가능한 ${invalidRuntimeControl.label} 목록에 없습니다.`
                  : `${invalidRuntimeControl.label}을(를) 선택하세요.`
                : `${runtimeSettingLabels}을 함께 저장합니다. 변경은 다음 세션 시작부터 적용됩니다.`}
          </p>
        </div>
      )}
      {provider && onConfigure && (
        <AgentSessionPersonaSettings
          session={session}
          provider={provider}
          canConfigure={canConfigure}
          onConfigure={onConfigure}
          onStatus={setActionStatus}
        />
      )}
      <AgentActivitySettings
        session={session}
        activityVisible={activityVisible}
        onActivityVisibilityChange={onActivityVisibilityChange}
        onConfigure={onConfigure}
        onStatus={setActionStatus}
      />
      {onControl && (
        <div className="dc-member-session-actions" aria-label={`${session.display_name} 세션 제어`}>
          <button
            type="button"
            className="dc-member-session-button"
            title="세션 시작"
            disabled={!canStart || Boolean(pendingAction)}
            onClick={() => void runControl("start")}
          >
            <Play size={15} />
            시작
          </button>
          <button
            type="button"
            className="dc-member-session-button"
            title="세션 일시정지"
            disabled={!canPause || Boolean(pendingAction)}
            onClick={() => void runControl("pause")}
          >
            <CirclePause size={15} />
            일시정지
          </button>
          <button
            type="button"
            className="dc-member-session-button"
            data-variant="danger"
            title="세션 중지"
            disabled={!canStop || Boolean(pendingAction)}
            onClick={() => void runControl("stop")}
          >
            <Square size={14} />
            중지
          </button>
          <button
            type="button"
            className="dc-member-session-button"
            title="세션 재개"
            disabled={!canResume || Boolean(pendingAction)}
            onClick={() => void runControl("resume")}
          >
            <RotateCcw size={15} />
            재개
          </button>
          <button
            type="button"
            className="dc-member-session-button"
            title="현재 응답 중단"
            disabled={!canInterrupt || Boolean(pendingAction)}
            onClick={() => void runControl("interrupt")}
          >
            <Zap size={15} />
            응답 중단
          </button>
        </div>
      )}
      {actionStatus && <p className="dc-member-session-status preserve-words">{actionStatus}</p>}
      <details className="dc-room-runtime-diagnostics preserve-words">
        <summary>고급 진단</summary>
        {session.runtime_profile_key && <p>profile {session.runtime_profile_key}</p>}
        {session.message_source && (
          <p>
            message {session.message_source}
            {session.message_source_strict ? " · strict" : ""}
          </p>
        )}
        <p>{latencySummary(session) || `turns ${session.turn_count || 0}`}</p>
        <p>cursor {session.last_seen_event_id || "none"}</p>
        <p>
          input {session.provider_visible_chars || 0} chars · {session.provider_visible_event_count || 0} events
        </p>
        <p>
          stderr {session.stderr_byte_count || 0} bytes · warnings {session.stderr_warning_count || 0}
        </p>
        {Boolean(session.notification_drop_count) && (
          <p className="dc-room-play-error">protocol drops {session.notification_drop_count}</p>
        )}
        {Boolean(session.adapter_activity_invalid_count) && (
          <p className="dc-room-play-error">
            invalid activity reports {session.adapter_activity_invalid_count}
          </p>
        )}
        {continuity && <p>{continuity}</p>}
        {typeof session.yolo_mode === "boolean" && (
          <p>approval {session.yolo_mode ? "unsafe always-approve" : session.approval_policy || "restricted"}</p>
        )}
        {Boolean(session.permission_request_count) && (
          <p>
            permissions denied {session.permission_denied_count || 0}/{session.permission_request_count}
          </p>
        )}
        {Boolean(session.denied_permission_names?.length) && (
          <p className="dc-room-play-error">
            denied: {session.denied_permission_names?.join(", ")}
          </p>
        )}
        {session.context_error_detected && <p className="dc-room-play-error">context error detected</p>}
        {session.provider_session_resume_error && (
          <p className="dc-room-play-error">{session.provider_session_resume_error}</p>
        )}
        {session.last_error && <p className="dc-room-play-error">{session.last_error}</p>}
      </details>
    </section>
  );
}

function controlDefault(provider: NativeCliProviderAvailability | undefined, key: string) {
  return provider?.controls?.find((control) => control.key === key)?.default_value || "";
}
