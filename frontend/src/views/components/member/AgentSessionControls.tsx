import { useState } from "react";
import { LogOut, Play, Square, Trash2 } from "lucide-react";
import {
  deleteLiveAgentSession,
  resumeAgentSession,
  resumeSelfManagedAgent,
  stopLiveAgentSessionAgent,
  stopSelfManagedAgent,
  type LiveAgentProcessGroup,
} from "../../../api";
import { isActivePresence } from "../../../lib/presenceStatus";
import {
  findProcessGroupForAgent,
  processGroupCanControlSingleAgent,
  processGroupIndividualControlReason,
  registeredAgentProcessGroupForAgent,
} from "../../../lib/liveAgentProcessControls";
import {
  agentRelaunchArguments,
  agentResumeHandle,
  agentSessionResumeStatus,
  agentExecutionMode,
  compactPathForDisplay,
  executionModeSummary,
  callModeAvailable,
  persistentModeAvailable,
  processStatusLabel,
  sessionLocationRows,
} from "./memberHelpers";
import type { MemberEntry } from "./memberTypes";

export default function AgentSessionControls({
  entry,
  agent,
  processGroups,
  onSessionActionComplete,
  onParticipantKick,
  onClose,
}: {
  entry: MemberEntry;
  agent: NonNullable<MemberEntry["agent"]>;
  processGroups: LiveAgentProcessGroup[];
  onSessionActionComplete?: () => void;
  onParticipantKick?: (participantId: string) => void | Promise<void>;
  onClose: () => void;
}) {
  const [sessionActionBusy, setSessionActionBusy] = useState(false);
  const [sessionActionStatus, setSessionActionStatus] = useState("");
  const processIdentity = {
    agent_id: agent.agent_id,
    display_name: agent.display_name,
  };
  const matchingProcessGroup = findProcessGroupForAgent(processGroups, processIdentity);
  const registeredSessionGroup = matchingProcessGroup
    ? undefined
    : registeredAgentProcessGroupForAgent(agent);
  const processGroup = matchingProcessGroup || registeredSessionGroup;
  const sessionIsRegisteredOnly = Boolean(registeredSessionGroup);
  const canControlSingleAgent = processGroupCanControlSingleAgent(processGroup, processIdentity);
  const processOwnsAgent = Boolean(processGroup);
  const individualControlReason = processGroupIndividualControlReason(
    processGroup,
    processIdentity,
    entry.displayName || "이 AI"
  );
  const processRunning = processGroup?.status === "running";
  const showIndividualControlReason = Boolean(individualControlReason && processRunning);
  const resumeActionLabel = sessionIsRegisteredOnly ? "START" : "RESUME";
  const hasResumeControl = Boolean(
    processGroup &&
      processOwnsAgent &&
      processGroup.group_id &&
      processGroup.meeting_id &&
      processGroup.config_path &&
      !processRunning
  );
  const hasStopControl = Boolean(
    processGroup &&
      canControlSingleAgent &&
      processGroup.group_id &&
      processGroup.meeting_id &&
      processRunning
  );
  const locationRows = sessionLocationRows(agent, processGroup);
  const hasSessionLocation = locationRows.length > 0;
  const hasSessionSection = !entry.agentSession && Boolean(
    hasSessionLocation || hasResumeControl || hasStopControl || showIndividualControlReason
  );
  const canonicalRoomAgent =
    Boolean(entry.agentSession) || agent.connection_kind === "native_cli_bridge";
  const hasRoomAdminControl = Boolean(
    agent.agent_id && agent.meeting_id && (onParticipantKick || !canonicalRoomAgent)
  );
  const canResumeSession = Boolean(hasResumeControl);
  const canStopSession = Boolean(hasStopControl);
  const selfRelaunchPid = Number(agent.relaunch_pid || 0);
  const selfRelaunchArgv = agentRelaunchArguments(agent);
  const isAgentOnline = isActivePresence(agent.status);
  const selfManaged = Boolean(
    entry.ownedByViewer && !processGroup && (selfRelaunchPid > 0 || selfRelaunchArgv.length > 0)
  );
  const canSelfStop = Boolean(selfManaged && isAgentOnline && selfRelaunchPid > 0);
  const canSelfResume = Boolean(selfManaged && !isAgentOnline && selfRelaunchArgv.length > 0);
  const executionMode = agentExecutionMode(agent);
  const canUseCallMode = callModeAvailable(agent);
  const canUsePersistentMode = persistentModeAvailable(agent);
  const executionSummary = executionModeSummary(agent);

  async function handleSelfStop() {
    if (!canSelfStop) return;
    setSessionActionBusy(true);
    setSessionActionStatus("STOP(종료) 요청 중...");
    try {
      await stopSelfManagedAgent({ agentId: agent.agent_id });
      setSessionActionStatus("STOP 완료 · 프로세스 종료됨");
      onSessionActionComplete?.();
    } catch (error) {
      setSessionActionStatus(error instanceof Error ? error.message : "STOP 실패");
    } finally {
      setSessionActionBusy(false);
    }
  }

  async function handleSelfResume() {
    if (!canSelfResume) return;
    setSessionActionBusy(true);
    setSessionActionStatus("RESUME(재실행) 요청 중...");
    try {
      await resumeSelfManagedAgent({ agentId: agent.agent_id });
      setSessionActionStatus("RESUME 완료 · 서버 백그라운드로 재실행됨");
      onSessionActionComplete?.();
    } catch (error) {
      setSessionActionStatus(error instanceof Error ? error.message : "RESUME 실패");
    } finally {
      setSessionActionBusy(false);
    }
  }

  async function handleResumeSession() {
    if (!processGroup || !canResumeSession) return;
    setSessionActionBusy(true);
    setSessionActionStatus(`${resumeActionLabel} 요청 중...`);
    try {
      const response = await resumeAgentSession({
        roomId: processGroup.meeting_id,
        agentId: agent.agent_id,
        sessionId: agentResumeHandle(agent),
        displayName: agent.display_name,
        providerKind: agent.provider_kind,
        sandbox: agent.sandbox_enforcement,
        permissions: agent.permission_option || agent.binding_permission_profile_id,
      });
      setSessionActionStatus(`${resumeActionLabel} 완료 · ${agentSessionResumeStatus(response)}`);
      onSessionActionComplete?.();
    } catch (error) {
      setSessionActionStatus(error instanceof Error ? error.message : "RESUME 실패");
    } finally {
      setSessionActionBusy(false);
    }
  }

  async function handleStopSession() {
    if (!processGroup || !canStopSession) return;
    setSessionActionBusy(true);
    setSessionActionStatus("STOP(KILL) 요청 중...");
    try {
      const response = await stopLiveAgentSessionAgent({
        meetingId: processGroup.meeting_id,
        groupId: processGroup.group_id,
        agentId: agent.agent_id,
      });
      setSessionActionStatus(
        `STOP(KILL) 완료${response.status ? ` · ${processStatusLabel(response.status)}` : ""}`
      );
      onSessionActionComplete?.();
    } catch (error) {
      setSessionActionStatus(error instanceof Error ? error.message : "STOP(KILL) 실패");
    } finally {
      setSessionActionBusy(false);
    }
  }

  async function handleExpelAgent() {
    if (!agent.agent_id || !onParticipantKick) return;
    if (!window.confirm(`${entry.displayName}을 이 방에서 추방할까요? 세션 설정은 유지됩니다.`)) return;
    setSessionActionBusy(true);
    setSessionActionStatus("추방 요청 중...");
    try {
      await onParticipantKick(agent.agent_id);
      setSessionActionStatus("추방 완료");
      onSessionActionComplete?.();
      onClose();
    } catch (error) {
      setSessionActionStatus(error instanceof Error ? error.message : "추방 실패");
    } finally {
      setSessionActionBusy(false);
    }
  }

  async function handleDeleteAgentSession() {
    const meetingId = processGroup?.meeting_id || agent.meeting_id;
    if (!meetingId || !agent.agent_id) return;
    const confirmed = window.confirm(
      `${entry.displayName} 세션을 삭제합니다. 방에서 제거되고 실행 중이면 중지되며 저장된 세션 설정도 삭제됩니다. 계속할까요?`
    );
    if (!confirmed) return;
    setSessionActionBusy(true);
    setSessionActionStatus("세션 삭제 요청 중...");
    try {
      await deleteLiveAgentSession({
        meetingId,
        groupId: processGroup?.group_id || agent.process_group_id,
        agentId: agent.agent_id,
      });
      setSessionActionStatus("세션 삭제 완료");
      onSessionActionComplete?.();
      onClose();
    } catch (error) {
      setSessionActionStatus(error instanceof Error ? error.message : "세션 삭제 실패");
    } finally {
      setSessionActionBusy(false);
    }
  }

  return (
    <>
      {hasSessionSection && (
        <section className="dc-member-detail-section" aria-label={`${entry.displayName} 세션 제어`}>
          <h3>세션 제어</h3>
          {hasSessionLocation ? (
            <div className="dc-member-session-location" aria-label={`${entry.displayName} 세션 위치`}>
              <div className="dc-member-session-location-head">
                <span>세션 위치</span>
                <span>{processStatusLabel(processGroup?.status)}</span>
              </div>
              <dl>
                {locationRows.map((row) => {
                  const value = String(row.value || "");
                  return (
                    <div key={row.label}>
                      <dt>{row.label}</dt>
                      <dd title={value}>{row.path ? compactPathForDisplay(value) : value}</dd>
                    </div>
                  );
                })}
              </dl>
            </div>
          ) : (
            <p className="dc-member-session-summary preserve-words">
              세션 위치 기록 없음 · {processStatusLabel(processGroup?.status)}
            </p>
          )}
          <div className="dc-member-execution-mode" aria-label={`${entry.displayName} 실행 방식`}>
            <div className="dc-member-execution-mode-head">
              <span>실행 방식</span>
              <span>Agent Session</span>
            </div>
            <div className="dc-member-execution-options" role="radiogroup" aria-label="에이전트 실행 방식">
              <button
                type="button"
                className="dc-member-execution-option"
                role="radio"
                aria-checked={executionMode === "baseline"}
                data-active={executionMode === "baseline"}
                disabled={!canUseCallMode}
                title="Agent Session state is attached; process execution is reported separately."
              >
                state attach
              </button>
              <button
                type="button"
                className="dc-member-execution-option"
                role="radio"
                aria-checked={executionMode === "runtime"}
                data-active={executionMode === "runtime"}
                disabled
                title="Agent Session process resume is experimental and reported separately."
              >
                process resume
              </button>
              <button
                type="button"
                className="dc-member-execution-option"
                role="radio"
                aria-checked={executionMode === "tool_loop"}
                data-active={executionMode === "tool_loop"}
                disabled
                title="Internal legacy loop; not a normal Agent Session choice."
              >
                internal loop
              </button>
              <button
                type="button"
                className="dc-member-execution-option"
                role="radio"
                aria-checked={executionMode === "tool_loop_unverified"}
                data-active={executionMode === "tool_loop_unverified"}
                disabled
                title="Requested execution path is not supported as a user-facing Agent Session."
              >
                unsupported
              </button>
              <button
                type="button"
                className="dc-member-execution-option"
                role="radio"
                aria-checked={executionMode === "persistent"}
                data-active={executionMode === "persistent"}
                disabled={!canUsePersistentMode}
                title={
                  canUsePersistentMode
                    ? "Agent Session has a verified persistent process proof."
                    : "Persistent execution is not exposed as a normal Agent Session choice yet."
                }
              >
                persistent proof
              </button>
            </div>
            <p className="dc-member-detail-note preserve-words">{executionSummary}</p>
            <p className="dc-member-detail-note preserve-words">
              Agent Session resume first attaches canonical room state. Process execution is reported separately.
            </p>
            <p className="dc-member-detail-note preserve-words">
              Normal room participation is ordered and turn-based. Unsupported loops stay internal.
            </p>
            <p className="dc-member-session-status preserve-words">
              state: attached · sandbox: {agent.sandbox_enforcement || "unknown"}
            </p>
          </div>
          {hasResumeControl || hasStopControl ? (
            <div className="dc-member-session-actions">
              {hasResumeControl && (
                <button
                  type="button"
                  className="dc-member-session-button"
                  disabled={!canResumeSession || sessionActionBusy}
                  onClick={handleResumeSession}
                >
                  <Play size={15} />
                  {resumeActionLabel}
                </button>
              )}
              {hasStopControl && (
                <button
                  type="button"
                  className="dc-member-session-button"
                  data-variant="danger"
                  disabled={!canStopSession || sessionActionBusy}
                  onClick={handleStopSession}
                >
                  <Square size={14} />
                  STOP(KILL)
                </button>
              )}
            </div>
          ) : (
            <p className="dc-member-detail-note preserve-words">{individualControlReason}</p>
          )}
          {sessionActionStatus && (
            <p className="dc-member-session-status preserve-words">{sessionActionStatus}</p>
          )}
        </section>
      )}
      {hasRoomAdminControl && (
        <section className="dc-member-detail-section" aria-label={`${entry.displayName} 방 관리`}>
          <h3>방 관리</h3>
          <p className="dc-member-detail-note preserve-words">
            {canonicalRoomAgent
              ? "추방하면 실행 중인 CLI를 중지하고 이 방의 참가자와 라우팅에서 제거합니다."
              : "추방은 이 방에서만 제거하고, 삭제는 저장된 레거시 세션 설정까지 제거합니다."}
          </p>
          {!hasResumeControl && !hasStopControl && !canSelfStop && !canSelfResume && (
            <p className="dc-member-detail-note preserve-words">
              이 세션은 서버가 직접 실행하는 프로세스도 아니고, 자기 실행 정보(pid/재실행 명령)도 등록하지 않아 여기서 멈추거나 재개할 수 없습니다.
            </p>
          )}
          {(canSelfStop || canSelfResume) && (
            <p className="dc-member-detail-note preserve-words">
              터미널에서 직접 띄운 내 에이전트입니다. STOP은 그 프로세스를 실제로 종료하고, RESUME은 서버 백그라운드로 다시 띄웁니다.
            </p>
          )}
          <div className="dc-member-session-actions">
            {canSelfStop && (
              <button
                type="button"
                className="dc-member-session-button"
                disabled={sessionActionBusy}
                onClick={() => void handleSelfStop()}
              >
                <Square size={15} />
                STOP
              </button>
            )}
            {canSelfResume && (
              <button
                type="button"
                className="dc-member-session-button"
                disabled={sessionActionBusy}
                onClick={() => void handleSelfResume()}
              >
                <Play size={15} />
                RESUME
              </button>
            )}
            {onParticipantKick && (
              <button
                type="button"
                className="dc-member-session-button"
                data-variant="danger"
                disabled={sessionActionBusy}
                onClick={handleExpelAgent}
              >
                <LogOut size={15} />
                추방
              </button>
            )}
            {!canonicalRoomAgent && (
              <button
                type="button"
                className="dc-member-session-button"
                data-variant="danger"
                disabled={sessionActionBusy}
                onClick={handleDeleteAgentSession}
              >
                <Trash2 size={15} />
                세션 삭제
              </button>
            )}
          </div>
          {!hasSessionSection && sessionActionStatus && (
            <p className="dc-member-session-status preserve-words">{sessionActionStatus}</p>
          )}
        </section>
      )}
    </>
  );
}
