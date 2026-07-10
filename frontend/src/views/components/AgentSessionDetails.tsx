import { useState } from "react";
import { CirclePause, Play, RotateCcw, Square, Zap } from "lucide-react";
import type { RoomAgentSession } from "../../api";

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
  onControl,
}: {
  session: RoomAgentSession;
  onControl?: (
    session: RoomAgentSession,
    action: AgentSessionControlAction
  ) => void | Promise<void>;
}) {
  const [pendingAction, setPendingAction] = useState<AgentSessionControlAction | null>(null);
  const [actionStatus, setActionStatus] = useState("");
  const status = session.runtime_status || session.status;
  const hasRunBefore = Boolean(
    session.started_at ||
      session.pid ||
      session.turn_count ||
      session.last_seen_event_id
  );
  const canStart =
    !hasRunBefore && ["", "available", "stopped", "error", "disconnected"].includes(status || "");
  const canPause = status === "idle";
  const canStop = agentSessionIsPresent(status) || status === "error";
  const canResume =
    status === "paused" ||
    (hasRunBefore && ["stopped", "error", "disconnected", "available"].includes(status || ""));
  const canInterrupt = status === "busy";
  const continuity = providerSessionContinuity(session);

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
          {session.pid ? (
            <div>
              <dt>Process</dt>
              <dd>{`pid ${session.pid}`}</dd>
            </div>
          ) : null}
        </dl>
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
