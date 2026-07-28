import { useEffect, useState } from "react";
import { Brain, CirclePause, Play, RotateCcw, Save, Square, Zap } from "lucide-react";
import type { RoomAgentSession } from "../../api";
import type { NativeCliProviderAvailability, ProviderControl } from "../../roomSocketClient";
import {
  effectiveProviderControlOptions,
  reconcileProviderSettings,
} from "../../lib/providerControlSettings";

export type AgentSessionControlAction =
  | "start"
  | "pause"
  | "stop"
  | "resume"
  | "interrupt";

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
  if (!session.provider_session_active && session.provider_session_load_supported) {
    return "provider session 재개 대기";
  }
  if (!session.provider_session_active) return "provider session 비활성";
  if (session.provider_session_resume_failed) return "provider session 새로 시작됨";
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

export default function AgentSessionDetails({
  session,
  provider,
  onControl,
  onConfigure,
  activityVisible = true,
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
  const status = session.runtime_status || session.status;
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
  const canConfigure = ["", "available", "stopped", "error", "disconnected"].includes(status || "");
  const runtimeSettingLabels =
    (provider?.controls || []).map((control) => control.label).join("·") || "런타임 설정";
  const invalidRuntimeControl = provider?.controls.find((control) =>
    !effectiveProviderControlOptions(provider, control, settings).some(
      (option) => option.value === (settings[control.key] ?? "")
    )
  );

  useEffect(() => {
    setSettings({
      model: session.model || controlDefault(provider, "model"),
      reasoning_effort:
        session.reasoning_effort || controlDefault(provider, "reasoning_effort"),
      service_tier: session.service_tier || controlDefault(provider, "service_tier"),
      variant: session.variant || controlDefault(provider, "variant"),
      permission_mode:
        session.permission_mode || controlDefault(provider, "permission_mode") || "meeting_read_only",
    });
  }, [
    provider,
    session.session_id,
    session.runtime_profile_key,
    session.model,
    session.reasoning_effort,
    session.service_tier,
    session.variant,
    session.permission_mode,
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
    if (!onConfigure || !canConfigure || invalidRuntimeControl || settingsBusy) return;
    setSettingsBusy(true);
    setActionStatus("");
    try {
      await onConfigure(session, settings);
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
    <section className="dc-member-detail-section" aria-label={`${session.display_name} Agent Session`}>
      <h3>Agent Session</h3>
      <div className="dc-member-session-location">
        <div className="dc-member-session-location-head">
          <span>실행 상태</span>
          <span>{agentSessionStatusLabel(status)}</span>
        </div>
        <dl>
          <div>
            <dt>Provider</dt>
            <dd>{session.provider_kind || "unknown"}</dd>
          </div>
          {session.model && (
            <div>
              <dt>Model</dt>
              <dd>{session.model}</dd>
            </div>
          )}
          <div>
            <dt>Runtime</dt>
            <dd>{`${session.runtime_kind || "live_cli"} · ${session.transport || "pty"}`}</dd>
          </div>
        </dl>
      </div>
      {provider && onConfigure && (
        <div className="dc-agent-runtime-settings" aria-label={`${session.display_name} 런타임 설정`}>
          {(provider.controls || []).map((control) => {
            const options = effectiveProviderControlOptions(provider, control, settings);
            return (
              <RuntimeSettingField
                key={`${session.session_id}:${control.key}`}
                control={control}
                options={options}
                value={settings[control.key] || ""}
                disabled={!canConfigure || settingsBusy}
                onChange={(value) => updateRuntimeSetting(control.key, value)}
              />
            );
          })}
          <button
            type="button"
            className="dc-member-session-button"
            disabled={!canConfigure || Boolean(invalidRuntimeControl) || settingsBusy}
            onClick={() => void saveSettings()}
          >
            <Save size={14} />
            런타임 설정 저장
          </button>
          <p className="preserve-words">
            {!canConfigure
              ? "현재 세션이 실행 중이라 시작 프로필을 표시하고 있습니다. 변경하려면 세션을 중지하세요."
              : invalidRuntimeControl
                ? `${invalidRuntimeControl.label}의 선택값을 확인하세요.`
                : `${runtimeSettingLabels}을 함께 저장합니다. 변경은 다음 세션 시작부터 적용됩니다.`}
          </p>
        </div>
      )}
      <div className="dc-agent-activity-setting">
        <div>
          <Brain size={15} aria-hidden />
          <span>생각과 작업 표시</span>
        </div>
        <label className="dc-agent-activity-toggle">
          <input
            type="checkbox"
            checked={activityVisible}
            disabled={!onActivityVisibilityChange}
            onChange={(event) => onActivityVisibilityChange?.(session, event.currentTarget.checked)}
          />
          <span>{activityVisible ? "켜짐" : "꺼짐"}</span>
        </label>
        <p>공개용 생각 요약과 안전하게 정리된 도구 활동만 표시합니다.</p>
      </div>
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

function RuntimeSettingField({
  control,
  options,
  value,
  disabled,
  onChange,
}: {
  control: ProviderControl;
  options: ProviderControl["options"];
  value: string;
  disabled: boolean;
  onChange: (value: string) => void;
}) {
  return (
    <label>
      <span>{control.label}</span>
      <select
        aria-label={control.label}
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(event.currentTarget.value)}
      >
        {!options.some((option) => option.value === value) && (
          <option value="" disabled>
            선택 필요
          </option>
        )}
        {options.map((option) => (
          <option key={`${control.key}:${option.value || "default"}`} value={option.value}>{option.label}</option>
        ))}
      </select>
    </label>
  );
}
