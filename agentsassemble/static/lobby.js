import {
  bindingSummary,
  displayTopic,
  escapeHtml,
  fetchJson,
  renderLifecycleBanner,
  roleMeta,
  setLiveAgentOperations,
  setLiveAgentProcesses,
  setLiveAgentSessionRuns,
  setLiveAgents,
  setLobbyEvents,
  state,
} from "./shared.js";

const lobbySides = new Set(["mine", "my-agent", "other", "other-agent"]);
let lobbyFeedHasPainted = false;
let lobbyFeedPinnedToLatest = true;
let pendingLobbyAttachments = [];
let lobbyAttachmentStatus = "";

export function renderLobby(options = {}) {
  const lobby = document.querySelector("#lobby");
  if (!lobby) return;
  const previousWindowScroll = readWindowScroll();
  const focusedId = document.activeElement?.id;
  const focusedSelection = readFocusedSelection(document.activeElement);
  const draftMessage = lobby.querySelector("#lobby-message")?.value || "";
  const processDraft = readLiveAgentProcessDraft(lobby);
  const registrationDraft = readLiveAgentRegistrationDraft(lobby);
  const previousFeed = lobby.querySelector(".lobby-feed");
  const previousScrollTop = previousFeed?.scrollTop || 0;
  const roster = buildLobbyRoster(state.lobbyEvents);
  const shouldFollowLatest = shouldFollowLobbyLatest(lobby, previousFeed, options);
  lobby.innerHTML = `
    <section class="lobby-layout">
      <div class="room-strip">
        <div>
          <span class="room-kicker">staging room</span>
          <strong>집결 로비</strong>
          <small>${escapeHtml(state.payload?.meeting ? displayTopic(state.payload.meeting) : "회의 준비")} · ${roster.length}명 · 에이전트 ${roster.reduce((count, user) => count + user.agents.length, 0)}</small>
        </div>
        <div class="room-actions">
          <span class="room-status">대기 중</span>
          <span class="room-status room-status-hot">투입 준비</span>
        </div>
      </div>
      ${renderLifecycleBanner(state.payload, { surface: "lobby" })}
      <div class="lobby-main">
        <div class="lobby-panel">
          ${renderLobbySummary(roster)}
          <div class="lobby-activity">
            <div class="lobby-feed-head">
              <div>
                <strong>최근 활동</strong>
                <span>공식 회의 전, 누가 준비됐고 누가 투입 대기인지 보는 비공식 기록입니다.</span>
              </div>
              <em>${state.lobbyEvents.length} events</em>
            </div>
            <div class="lobby-feed">
              ${state.lobbyEvents.length ? state.lobbyEvents.map(renderLobbyEvent).join("") : '<p class="lobby-empty">아직 로비 메시지가 없습니다.</p>'}
            </div>
          </div>
          <form id="lobby-form" class="lobby-form">
            <input id="lobby-message" maxlength="2000" placeholder="메시지를 입력하세요" />
            <label class="lobby-file-button" title="파일 첨부">
              <input id="lobby-attachments" type="file" multiple />
              첨부
            </label>
            ${hasRemoteLobbyBridge() ? '<button type="button" id="lobby-ask-remote">원격 호출</button>' : ""}
            <button type="submit">보내기</button>
            ${renderPendingLobbyAttachments()}
          </form>
        </div>
        ${renderLobbyRoster(roster)}
      </div>
    </section>
  `;
  restoreLiveAgentProcessDraft(lobby, processDraft);
  restoreLiveAgentRegistrationDraft(lobby, registrationDraft);
  const form = lobby.querySelector("#lobby-form");
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    await sendLobbyEvent("message");
  });
  const messageInput = lobby.querySelector("#lobby-message");
  if (messageInput && focusedId === "lobby-message") {
    messageInput.value = draftMessage;
    messageInput.focus();
  }
  restoreFocusedLiveAgentField(lobby, focusedId, focusedSelection);
  messageInput?.addEventListener("keydown", async (event) => {
    if (event.key !== "Enter" || event.isComposing) return;
    event.preventDefault();
    await sendLobbyEvent("message");
  });
  const askRemoteButton = lobby.querySelector("#lobby-ask-remote");
  askRemoteButton?.addEventListener("click", async () => {
    await sendLobbyEvent("message", { askRemote: true });
  });
  lobby.querySelector("#lobby-attachments")?.addEventListener("change", async (event) => {
    await uploadLobbyAttachments(event.currentTarget.files);
    event.currentTarget.value = "";
  });
  lobby.querySelectorAll("[data-remove-lobby-attachment]").forEach((button) => {
    button.addEventListener("click", () => removePendingLobbyAttachment(button.dataset.removeLobbyAttachment));
  });
  bindLobbyAttachmentPreview(lobby);
  bindLobbyFeedScroll(lobby);
  const myNameInput = lobby.querySelector("#lobby-my-name");
  myNameInput?.addEventListener("input", () => {
    localStorage.setItem("agentsassemble.name", myNameInput.value.trim());
  });
  lobby.querySelectorAll("[data-lobby-action]").forEach((button) => {
    button.addEventListener("click", () => sendLobbyAction(button));
  });
  lobby.querySelector("#provider-health-check")?.addEventListener("click", async () => {
    await runProviderHealthCheck();
  });
  lobby.querySelector("#live-agent-refresh")?.addEventListener("click", () => {
    loadLiveAgents({ force: true });
    loadLiveAgentHealth({ force: true });
  });
  lobby.querySelector("#live-agent-process-refresh")?.addEventListener("click", () => {
    loadLiveAgentHealth({ force: true });
    loadLiveAgentProcesses({ force: true });
    loadLiveAgentProcessEvents({ force: true });
    loadLiveAgentOperations({ force: true });
    loadLiveAgentSessionRuns({ force: true });
  });
  lobby.querySelector("#live-agent-process-smoke")?.addEventListener("click", async () => {
    await runLiveAgentSmoke(lobby);
  });
  lobby.querySelector("#live-agent-official-round-smoke")?.addEventListener("click", async () => {
    await runLiveAgentOfficialRoundSmoke(lobby);
  });
  lobby.querySelector("#live-agent-session-smoke")?.addEventListener("click", async () => {
    await runLiveAgentSessionSmoke(lobby);
  });
  lobby.querySelector("#live-agent-readiness-check")?.addEventListener("click", async () => {
    await runLiveAgentReadiness(lobby);
  });
  lobby.querySelector("#live-agent-preflight-check")?.addEventListener("click", async () => {
    await runLiveAgentPreflight(lobby);
  });
  lobby.querySelector("#live-agent-discover")?.addEventListener("click", async () => {
    await runLiveAgentDiscovery(lobby);
  });
  lobby.querySelector("#live-agent-auto-join")?.addEventListener("click", async () => {
    await runLiveAgentAutoJoin(lobby);
  });
  lobby.querySelector("#live-agent-process-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    await startLiveAgentProcessGroup(event.currentTarget);
  });
  lobby.querySelector("#live-agent-process-stop-running")?.addEventListener("click", async () => {
    await stopRunningLiveAgentProcessGroups();
  });
  lobby.querySelector("#live-agent-session-start")?.addEventListener("click", async () => {
    await startLiveAgentSession(lobby);
  });
  lobby.querySelector("#live-agent-session-ensure")?.addEventListener("click", async () => {
    await ensureLiveAgentSession(lobby);
  });
  lobby.querySelector("#live-agent-session-run-ensure")?.addEventListener("click", async () => {
    await ensureLiveAgentSessionRun(lobby);
  });
  lobby.querySelector("#live-agent-session-resume")?.addEventListener("click", async () => {
    await resumeLiveAgentSession(lobby);
  });
  lobby.querySelector("#live-agent-session-restart")?.addEventListener("click", async () => {
    await restartLiveAgentSession(lobby);
  });
  lobby.querySelector("#live-agent-session-recover")?.addEventListener("click", async () => {
    await recoverLiveAgentSession(lobby);
  });
  lobby.querySelector("#live-agent-session-check")?.addEventListener("click", async () => {
    await checkLiveAgentSession(lobby);
  });
  lobby.querySelector("#live-agent-session-stop")?.addEventListener("click", async () => {
    await stopLiveAgentSession(lobby);
  });
  lobby.querySelector("#live-agent-flow-start")?.addEventListener("click", async () => {
    await startLiveAgentFlow(lobby);
  });
  lobby.querySelector("#live-agent-flow-stop")?.addEventListener("click", async () => {
    await stopLiveAgentFlow(lobby);
  });
  lobby.querySelector("#live-agent-call-round")?.addEventListener("click", async () => {
    await callLiveAgentOfficialRound(lobby);
  });
  lobby.querySelector("#live-agent-call-remaining-rounds")?.addEventListener("click", async () => {
    await callLiveAgentRemainingRounds(lobby);
  });
  lobby.querySelector("#live-agent-review-checkpoint")?.addEventListener("click", async () => {
    await callLiveAgentReviewCheckpoint(lobby);
  });
  lobby.querySelectorAll("[data-live-agent-review-checkpoint-input]").forEach((input) => {
    input.addEventListener("keydown", async (event) => {
      if (event.key !== "Enter" || event.isComposing) return;
      event.preventDefault();
      await callLiveAgentReviewCheckpoint(lobby);
    });
  });
  lobby.querySelectorAll("[data-live-agent-process-stop]").forEach((button) => {
    button.addEventListener("click", () => stopLiveAgentProcessGroup(button.dataset.liveAgentProcessStop));
  });
  lobby.querySelectorAll("[data-live-agent-process-restart]").forEach((button) => {
    button.addEventListener("click", () => restartLiveAgentProcessGroup(button.dataset.liveAgentProcessRestart));
  });
  lobby.querySelectorAll(".live-agent-process-recover").forEach((button) => {
    button.addEventListener("click", () => recoverLiveAgentProcessGroup(button.dataset.liveAgentProcessRecover));
  });
  lobby.querySelectorAll("[data-live-agent-session-run-retry-now]").forEach((button) => {
    button.addEventListener("click", () => retryLiveAgentSessionRunNow(button.dataset.liveAgentSessionRunRetryNow));
  });
  lobby.querySelectorAll("[data-live-agent-session-run-pause]").forEach((button) => {
    button.addEventListener("click", () => pauseLiveAgentSessionRun(button.dataset.liveAgentSessionRunPause));
  });
  lobby.querySelectorAll("[data-live-agent-session-run-resume]").forEach((button) => {
    button.addEventListener("click", () => resumeLiveAgentSessionRun(button.dataset.liveAgentSessionRunResume));
  });
  lobby.querySelectorAll("[data-live-agent-session-run-stop]").forEach((button) => {
    button.addEventListener("click", () => stopLiveAgentSessionRun(button.dataset.liveAgentSessionRunStop));
  });
  lobby.querySelectorAll("[data-live-agent-engagement]").forEach((select) => {
    select.addEventListener("change", () => updateLiveAgentEngagement(select.dataset.liveAgentEngagement, select.value));
  });
  lobby.querySelectorAll("[data-live-agent-probe]").forEach((button) => {
    button.addEventListener("click", () => runLiveAgentProbe(button.dataset.liveAgentProbe));
  });
  lobby.querySelector("#live-agent-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    await sendLiveAgentRegistration(event.currentTarget);
  });
  lobby.querySelector("#live-agent-join-brief")?.addEventListener("click", async () => {
    await generateLiveAgentJoinBrief(lobby);
  });
  lobby.querySelector("#codex-session-refresh")?.addEventListener("click", () => {
    loadCodexSessions({ force: true });
  });
  lobby.querySelector("#codex-session-join")?.addEventListener("click", async () => {
    await sendCodexSessionJoin(lobby.querySelector("#codex-invite-form"));
  });
  lobby.querySelector("#codex-invite-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    await sendCodexSessionInvite(event.currentTarget);
  });
  if (!state.codexSessionsLoaded && !state.codexSessionsLoading) {
    loadCodexSessions({ background: true });
  }
  if (!state.liveAgentsLoaded && !state.liveAgentsLoading) {
    loadLiveAgents({ background: true });
  }
  if (!state.liveAgentHealthLoaded && !state.liveAgentHealthLoading) {
    loadLiveAgentHealth({ background: true });
  }
  if (!state.liveAgentProcessesLoaded && !state.liveAgentProcessesLoading) {
    loadLiveAgentProcesses({ background: true });
  }
  if (!state.liveAgentProcessEventsLoaded && !state.liveAgentProcessEventsLoading) {
    loadLiveAgentProcessEvents({ background: true });
  }
  if (!state.liveAgentOperationsLoaded && !state.liveAgentOperationsLoading) {
    loadLiveAgentOperations({ background: true });
  }
  if (!state.liveAgentSessionRunsLoaded && !state.liveAgentSessionRunsLoading) {
    loadLiveAgentSessionRuns({ background: true });
  }
  if (!state.liveAgentFlowLoaded && !state.liveAgentFlowLoading) {
    loadLiveAgentFlow({ background: true });
  }
  if (shouldFollowLatest) scrollLobbyFeedToLatest(lobby);
  else restoreLobbyFeedScroll(lobby, previousScrollTop);
  restoreWindowScroll(previousWindowScroll);
  bindLobbyAttachmentPreview(lobby);
  lobbyFeedHasPainted = true;
}

export function refreshLobbyFeed(options = {}) {
  const lobby = document.querySelector("#lobby");
  const feed = lobby?.querySelector(".lobby-feed");
  if (!lobby || !feed) {
    renderLobby(options);
    return;
  }
  const previousScrollTop = feed.scrollTop;
  const shouldFollowLatest = shouldFollowLobbyLatest(lobby, feed, options);
  const events = state.lobbyEvents || [];
  const existing = new Map(
    Array.from(feed.querySelectorAll("[data-lobby-event-id]")).map((element) => [element.dataset.lobbyEventId, element])
  );
  const eventIds = new Set();
  if (!events.length) {
    feed.innerHTML = '<p class="lobby-empty">아직 로비 메시지가 없습니다.</p>';
  } else {
    feed.querySelector(".lobby-empty")?.remove();
    for (const event of events) {
      const eventId = String(event.id || "").trim();
      if (!eventId) {
        renderLobby(options);
        return;
      }
      eventIds.add(eventId);
      const element = existing.get(eventId);
      if (element) {
        const signature = lobbyEventSignature(event);
        if (element.dataset.lobbyEventSignature !== signature) updateLobbyEventElement(element, event, signature);
        continue;
      }
      feed.insertAdjacentHTML("beforeend", renderLobbyEvent(event));
    }
    for (const [eventId, element] of existing.entries()) {
      if (!eventIds.has(eventId)) element.remove();
    }
  }
  const count = lobby.querySelector(".lobby-feed-head em");
  if (count) count.textContent = `${events.length} events`;
  if (shouldFollowLatest) scrollLobbyFeedToLatest(lobby);
  else restoreLobbyFeedScroll(lobby, previousScrollTop);
  bindLobbyAttachmentPreview(lobby);
}

function readLiveAgentProcessDraft(lobby) {
  const form = lobby.querySelector("#live-agent-process-form");
  if (!form) return null;
  return {
    configPath: form.querySelector("#live-agent-process-config")?.value ?? "",
    groupId: form.querySelector("#live-agent-process-group")?.value ?? "",
    meetingId: form.querySelector("#live-agent-session-meeting-id")?.value ?? "",
    councilConfig: form.querySelector("#live-agent-session-council-config")?.value ?? "",
    agentConfig: form.querySelector("#live-agent-session-agent-config")?.value ?? "",
    connectTimeout: form.querySelector("#live-agent-session-connect-timeout")?.value ?? "",
    roundId: form.querySelector("#live-agent-round-id")?.value ?? "",
    meetingDefault: form.querySelector("#live-agent-session-meeting-id")?.dataset.defaultValue ?? "",
    roundDefault: form.querySelector("#live-agent-round-id")?.dataset.defaultValue ?? "",
    roundTimeout: form.querySelector("#live-agent-round-timeout")?.value ?? "",
    roundMaxRounds: form.querySelector("#live-agent-round-max-rounds")?.value ?? "",
    roundStopOnTimeout: Boolean(form.querySelector("#live-agent-round-stop-on-timeout")?.checked),
    flowTopic: form.querySelector("#live-agent-flow-topic")?.value ?? "",
    flowDuration: form.querySelector("#live-agent-flow-duration")?.value ?? "",
    reviewCheckpointMessage: form.querySelector("#live-agent-review-checkpoint-message")?.value ?? "",
    reviewCheckpointId: form.querySelector("#live-agent-review-checkpoint-id")?.value ?? "",
    reviewCheckpointTimeout: form.querySelector("#live-agent-review-checkpoint-timeout")?.value ?? "",
    sessionRunRemainingRounds: Boolean(form.querySelector("#live-agent-session-run-remaining-rounds")?.checked),
    sessionProbeBoundAgents: Boolean(form.querySelector("#live-agent-session-probe-bound-agents")?.checked),
    sessionProbeTimeout: form.querySelector("#live-agent-session-probe-timeout")?.value ?? "",
    discoverySessionBundle: Boolean(form.querySelector("#live-agent-discovery-session-bundle")?.checked),
    discoveryApprovedAgents: liveAgentDiscoverySelectedApprovals(lobby),
    realProviderApproval: Boolean(form.querySelector("#live-agent-auto-join-real-provider-approval")?.checked),
    autoRestart: Boolean(form.querySelector("#live-agent-process-auto-restart")?.checked),
    officialRoundSmoke: Boolean(form.querySelector("#live-agent-readiness-official-round")?.checked),
    sessionSmoke: Boolean(form.querySelector("#live-agent-readiness-session-smoke")?.checked),
    sessionSmokeSoakCycles: form.querySelector("#live-agent-session-smoke-soak-cycles")?.value ?? "",
    sessionSmokeSoakInterval: form.querySelector("#live-agent-session-smoke-soak-interval")?.value ?? "",
    maxRestarts: form.querySelector("#live-agent-process-max-restarts")?.value ?? "",
    restartBackoff: form.querySelector("#live-agent-process-restart-backoff")?.value ?? "",
    staleRestartAfter: form.querySelector("#live-agent-process-stale-restart-after")?.value ?? "",
  };
}

function readLiveAgentRegistrationDraft(lobby) {
  const form = lobby.querySelector("#live-agent-form");
  if (!form) return null;
  return {
    agentId: form.querySelector("#live-agent-id")?.value ?? "",
    displayName: form.querySelector("#live-agent-display-name")?.value ?? "",
    providerKind: form.querySelector("#live-agent-provider-kind")?.value ?? "",
    connectionKind: form.querySelector("#live-agent-connection-kind")?.value ?? "",
  };
}

function restoreLiveAgentProcessDraft(lobby, draft) {
  if (!draft) return;
  const config = lobby.querySelector("#live-agent-process-config");
  const group = lobby.querySelector("#live-agent-process-group");
  const meetingId = lobby.querySelector("#live-agent-session-meeting-id");
  const councilConfig = lobby.querySelector("#live-agent-session-council-config");
  const agentConfig = lobby.querySelector("#live-agent-session-agent-config");
  const connectTimeout = lobby.querySelector("#live-agent-session-connect-timeout");
  const roundId = lobby.querySelector("#live-agent-round-id");
  const roundTimeout = lobby.querySelector("#live-agent-round-timeout");
  const roundMaxRounds = lobby.querySelector("#live-agent-round-max-rounds");
  const roundStopOnTimeout = lobby.querySelector("#live-agent-round-stop-on-timeout");
  const flowTopic = lobby.querySelector("#live-agent-flow-topic");
  const flowDuration = lobby.querySelector("#live-agent-flow-duration");
  const reviewCheckpointMessage = lobby.querySelector("#live-agent-review-checkpoint-message");
  const reviewCheckpointId = lobby.querySelector("#live-agent-review-checkpoint-id");
  const reviewCheckpointTimeout = lobby.querySelector("#live-agent-review-checkpoint-timeout");
  const sessionRunRemainingRounds = lobby.querySelector("#live-agent-session-run-remaining-rounds");
  const sessionProbeBoundAgents = lobby.querySelector("#live-agent-session-probe-bound-agents");
  const sessionProbeTimeout = lobby.querySelector("#live-agent-session-probe-timeout");
  const discoverySessionBundle = lobby.querySelector("#live-agent-discovery-session-bundle");
  const realProviderApproval = lobby.querySelector("#live-agent-auto-join-real-provider-approval");
  const autoRestart = lobby.querySelector("#live-agent-process-auto-restart");
  const officialRoundSmoke = lobby.querySelector("#live-agent-readiness-official-round");
  const sessionSmoke = lobby.querySelector("#live-agent-readiness-session-smoke");
  const sessionSmokeSoakCycles = lobby.querySelector("#live-agent-session-smoke-soak-cycles");
  const sessionSmokeSoakInterval = lobby.querySelector("#live-agent-session-smoke-soak-interval");
  const maxRestarts = lobby.querySelector("#live-agent-process-max-restarts");
  const restartBackoff = lobby.querySelector("#live-agent-process-restart-backoff");
  const staleRestartAfter = lobby.querySelector("#live-agent-process-stale-restart-after");
  if (config) config.value = draft.configPath;
  if (group) group.value = draft.groupId;
  restoreDefaultedInput(meetingId, draft.meetingId, draft.meetingDefault);
  if (councilConfig) councilConfig.value = draft.councilConfig;
  if (agentConfig) agentConfig.value = draft.agentConfig;
  if (connectTimeout) connectTimeout.value = draft.connectTimeout;
  restoreDefaultedInput(roundId, draft.roundId, draft.roundDefault);
  if (roundTimeout) roundTimeout.value = draft.roundTimeout;
  if (roundMaxRounds) roundMaxRounds.value = draft.roundMaxRounds;
  if (roundStopOnTimeout) roundStopOnTimeout.checked = draft.roundStopOnTimeout;
  if (flowTopic) flowTopic.value = draft.flowTopic;
  if (flowDuration) flowDuration.value = draft.flowDuration;
  if (reviewCheckpointMessage) reviewCheckpointMessage.value = draft.reviewCheckpointMessage;
  if (reviewCheckpointId) reviewCheckpointId.value = draft.reviewCheckpointId;
  if (reviewCheckpointTimeout) reviewCheckpointTimeout.value = draft.reviewCheckpointTimeout;
  if (sessionRunRemainingRounds) sessionRunRemainingRounds.checked = draft.sessionRunRemainingRounds;
  if (sessionProbeBoundAgents) sessionProbeBoundAgents.checked = draft.sessionProbeBoundAgents;
  if (sessionProbeTimeout) sessionProbeTimeout.value = draft.sessionProbeTimeout;
  if (discoverySessionBundle) discoverySessionBundle.checked = draft.discoverySessionBundle;
  restoreLiveAgentDiscoverySelectedApprovals(lobby, draft.discoveryApprovedAgents);
  if (realProviderApproval) realProviderApproval.checked = draft.realProviderApproval;
  if (autoRestart) autoRestart.checked = draft.autoRestart;
  if (officialRoundSmoke) officialRoundSmoke.checked = draft.officialRoundSmoke;
  if (sessionSmoke) sessionSmoke.checked = draft.sessionSmoke;
  if (sessionSmokeSoakCycles) sessionSmokeSoakCycles.value = draft.sessionSmokeSoakCycles;
  if (sessionSmokeSoakInterval) sessionSmokeSoakInterval.value = draft.sessionSmokeSoakInterval;
  if (maxRestarts) maxRestarts.value = draft.maxRestarts;
  if (restartBackoff) restartBackoff.value = draft.restartBackoff;
  if (staleRestartAfter) staleRestartAfter.value = draft.staleRestartAfter;
}

function restoreDefaultedInput(input, draftValue, previousDefault) {
  if (!input) return;
  const nextDefault = input.dataset.defaultValue || "";
  if (draftValue === previousDefault) {
    input.value = nextDefault;
    return;
  }
  input.value = draftValue;
}

function restoreLiveAgentRegistrationDraft(lobby, draft) {
  if (!draft) return;
  const agentId = lobby.querySelector("#live-agent-id");
  const displayName = lobby.querySelector("#live-agent-display-name");
  const providerKind = lobby.querySelector("#live-agent-provider-kind");
  const connectionKind = lobby.querySelector("#live-agent-connection-kind");
  if (agentId) agentId.value = draft.agentId;
  if (displayName) displayName.value = draft.displayName;
  if (providerKind && draft.providerKind) providerKind.value = draft.providerKind;
  if (connectionKind && draft.connectionKind) connectionKind.value = draft.connectionKind;
}

function readFocusedSelection(element) {
  if (!element?.id || typeof element.selectionStart !== "number") return null;
  return {
    start: element.selectionStart,
    end: element.selectionEnd,
    direction: element.selectionDirection || "none",
  };
}

function restoreFocusedLiveAgentField(lobby, focusedId, selection) {
  if (!focusedId?.startsWith("live-agent-")) return;
  const focused = lobby.querySelector(`#${focusedId}`);
  if (!focused) return;
  focused.focus();
  if (selection && typeof focused.setSelectionRange === "function") {
    focused.setSelectionRange(selection.start, selection.end, selection.direction);
  }
}

function isLobbyFeedNearBottom(lobby) {
  const feed = lobby.querySelector(".lobby-feed");
  if (!feed) return true;
  const distanceFromBottom = feed.scrollHeight - feed.scrollTop - feed.clientHeight;
  return distanceFromBottom < 48;
}

function shouldFollowLobbyLatest(lobby, previousFeed, options = {}) {
  if (!lobbyFeedHasPainted || !previousFeed) return true;
  const requestedFollowLatest = options.followLatest ?? isLobbyFeedNearBottom(lobby);
  if (requestedFollowLatest === true) return true;
  if (requestedFollowLatest === false) return lobbyFeedPinnedToLatest;
  return lobbyFeedPinnedToLatest || requestedFollowLatest;
}

function bindLobbyFeedScroll(lobby) {
  const feed = lobby.querySelector(".lobby-feed");
  if (!feed) return;
  feed.addEventListener(
    "scroll",
    () => {
      lobbyFeedPinnedToLatest = isLobbyFeedNearBottom(lobby);
    },
    { passive: true }
  );
}

function scrollLobbyFeedToLatest(lobby) {
  const feed = lobby.querySelector(".lobby-feed");
  if (!feed) return;
  lobbyFeedPinnedToLatest = true;
  applyScrollPosition(() => {
    feed.scrollTop = feed.scrollHeight;
  });
}

function restoreLobbyFeedScroll(lobby, scrollTop) {
  const feed = lobby.querySelector(".lobby-feed");
  if (!feed) return;
  lobbyFeedPinnedToLatest = false;
  applyScrollPosition(() => {
    feed.scrollTop = scrollTop;
  });
}

function applyScrollPosition(write) {
  write();
  requestAnimationFrame(write);
  setTimeout(write, 0);
  setTimeout(write, 120);
}

function readWindowScroll() {
  if (typeof window === "undefined") return null;
  return { x: window.scrollX || 0, y: window.scrollY || 0 };
}

function restoreWindowScroll(position) {
  if (!position || typeof window === "undefined") return;
  window.scrollTo(position.x, position.y);
  requestAnimationFrame(() => {
    window.scrollTo(position.x, position.y);
  });
}

function renderLobbySummary(roster) {
  const memberCount = roster.length;
  const agentCount = roster.reduce((count, user) => count + user.agents.length, 0);
  const deployedCount = roster.reduce(
    (count, user) => count + user.agents.filter((agent) => agent.deploy).length,
    0
  );
  return `
    <section class="lobby-summary" aria-label="로비 요약">
      ${renderSummaryMetric("참여자", memberCount)}
      ${renderSummaryMetric("에이전트", agentCount)}
      ${renderSummaryMetric("투입 대기", deployedCount)}
      ${renderSummaryMetric("이벤트", state.lobbyEvents.length)}
    </section>
  `;
}

function hasRemoteLobbyBridge() {
  const meeting = state.payload?.meeting;
  const providers = providerList(meeting?.provider_configs);
  const remoteProviderIds = new Set(
    providers.filter((provider) => provider.kind === "remote_http_bridge").map((provider) => provider.id)
  );
  return (meeting?.agent_bindings || []).some((binding) => remoteProviderIds.has(binding.provider_id));
}

function providerList(providerConfigs) {
  if (Array.isArray(providerConfigs)) return providerConfigs;
  if (providerConfigs && typeof providerConfigs === "object") return Object.values(providerConfigs);
  return [];
}

function renderSummaryMetric(label, value) {
  return `<div class="summary-metric"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`;
}

function renderPendingLobbyAttachments() {
  if (!pendingLobbyAttachments.length && !lobbyAttachmentStatus) return "";
  const items = pendingLobbyAttachments
    .map(
      (attachment) => `
        <span class="lobby-attachment-chip">
          ${escapeHtml(attachment.filename || "attachment")}
          <button type="button" data-remove-lobby-attachment="${escapeHtml(attachment.id || "")}" aria-label="첨부 제거">×</button>
        </span>
      `
    )
    .join("");
  const status = lobbyAttachmentStatus ? `<small>${escapeHtml(lobbyAttachmentStatus)}</small>` : "";
  return `<div class="lobby-attachment-draft">${items}${status}</div>`;
}

function renderLobbyEvent(event) {
  const eventId = String(event.id || "");
  const signature = lobbyEventSignature(event);
  return `
    <article class="${escapeHtml(lobbyEventClassName(event))}" data-lobby-event-id="${escapeHtml(eventId)}" data-lobby-event-signature="${escapeHtml(signature)}">
      ${renderLobbyEventBody(event)}
    </article>
  `;
}

function updateLobbyEventElement(element, event, signature = lobbyEventSignature(event)) {
  element.setAttribute("class", lobbyEventClassName(event));
  element.setAttribute("data-lobby-event-id", String(event.id || ""));
  element.setAttribute("data-lobby-event-signature", signature);
  element.innerHTML = renderLobbyEventBody(event);
}

function lobbyEventClassName(event) {
  const currentName = localStorage.getItem("agentsassemble.name") || "";
  const storedSide = lobbySides.has(event.side) ? event.side : "";
  const side = storedSide || (currentName && event.name === currentName ? "mine" : "other");
  return `lobby-event lobby-${event.kind || "message"} lobby-${side}`;
}

function renderLobbyEventBody(event) {
  const currentName = localStorage.getItem("agentsassemble.name") || "";
  const storedSide = lobbySides.has(event.side) ? event.side : "";
  const side = storedSide || (currentName && event.name === currentName ? "mine" : "other");
  const content = event.message || defaultLobbyMessage(event.kind, side);
  const name = event.name || "guest";
  const sideLabel = lobbySideLabel(side);
  const showSideLabel = name !== sideLabel;
  return `
    <div class="lobby-avatar">${escapeHtml(initials(name))}</div>
    <div class="lobby-bubble">
      <div class="lobby-meta">
        <strong>${escapeHtml(name)}</strong>
        ${showSideLabel ? `<span>${escapeHtml(sideLabel)}</span>` : ""}
        <span>${escapeHtml(lobbyKindLabel(event.kind))}</span>
      </div>
      <p>${escapeHtml(content)}</p>
      ${renderLobbyAttachments(event.attachments)}
    </div>
  `;
}

function lobbyEventSignature(event) {
  return stableUiSignature(event);
}

function stableUiSignature(value) {
  const text = JSON.stringify(value || {});
  let hash = 2166136261;
  for (let index = 0; index < text.length; index += 1) {
    hash ^= text.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0).toString(16);
}

function renderLobbyAttachments(attachments) {
  if (!Array.isArray(attachments) || !attachments.length) return "";
  return `
    <div class="lobby-attachments">
      ${attachments.map(renderLobbyAttachment).join("")}
    </div>
  `;
}

function renderLobbyAttachment(attachment) {
  const filename = attachment?.filename || "attachment";
  const url = attachment?.url || attachment?.download_url || "";
  const downloadUrl = attachment?.download_url || url;
  if (attachment?.is_image && url) {
    return `
      <button type="button" class="lobby-attachment-thumb" data-attachment-preview="${escapeHtml(url)}" data-attachment-filename="${escapeHtml(filename)}">
        <img src="${escapeHtml(url)}" alt="${escapeHtml(filename)}" loading="lazy" />
        <span>${escapeHtml(filename)}</span>
      </button>
    `;
  }
  return `
    <a class="lobby-attachment-file" href="${escapeHtml(downloadUrl)}" download="${escapeHtml(filename)}">
      <span>${escapeHtml(filename)}</span>
      <small>${escapeHtml(formatAttachmentSize(attachment?.size))}</small>
    </a>
  `;
}

function formatAttachmentSize(size) {
  const bytes = Number(size || 0);
  if (!Number.isFinite(bytes) || bytes <= 0) return "file";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 102.4) / 10} KB`;
  return `${Math.round(bytes / (1024 * 102.4)) / 10} MB`;
}

function bindLobbyAttachmentPreview(root) {
  root?.querySelectorAll("[data-attachment-preview]").forEach((button) => {
    if (button.dataset.previewBound === "true") return;
    button.dataset.previewBound = "true";
    button.addEventListener("click", () => {
      openAttachmentPreview(button.dataset.attachmentPreview, button.dataset.attachmentFilename || "image");
    });
  });
}

function openAttachmentPreview(url, filename) {
  if (!url || !document.body || typeof document.createElement !== "function") return;
  const previous = document.querySelector(".lobby-attachment-preview");
  previous?.remove();
  const overlay = document.createElement("div");
  overlay.className = "lobby-attachment-preview";
  overlay.innerHTML = `
    <button type="button" class="lobby-attachment-preview-close" aria-label="미리보기 닫기">×</button>
    <figure>
      <img src="${escapeHtml(url)}" alt="${escapeHtml(filename)}" />
      <figcaption>${escapeHtml(filename)}</figcaption>
    </figure>
  `;
  overlay.addEventListener("click", (event) => {
    if (event.target === overlay || event.target?.classList?.contains("lobby-attachment-preview-close")) {
      overlay.remove();
    }
  });
  document.body.appendChild(overlay);
}

function buildLobbyRoster(events) {
  const users = new Map();
  for (const event of events) {
    const side = lobbySides.has(event.side) ? event.side : "other";
    const name = String(event.name || defaultLobbyName(side)).trim() || defaultLobbyName(side);
    const isAgent = side === "my-agent" || side === "other-agent";
    const ownerKey = side === "mine" || side === "my-agent" ? "mine" : "other";
    const ownerName = ownerKey === "mine" ? "나" : "상대";
    if (!users.has(ownerKey)) {
      users.set(ownerKey, { key: ownerKey, name: ownerName, messageCount: 0, agents: new Map() });
    }
    const user = users.get(ownerKey);
    user.messageCount += 1;
    if (isAgent) {
      const agent = user.agents.get(name) || { name, messageCount: 0, ready: false, deploy: false };
      agent.messageCount += 1;
      agent.ready = agent.ready || event.kind === "ready";
      agent.deploy = agent.deploy || event.kind === "deploy";
      user.agents.set(name, agent);
    }
  }
  return Array.from(users.values()).map((user) => ({ ...user, agents: Array.from(user.agents.values()) }));
}

function renderLobbyRoster(roster) {
  const participantCount = roster.length;
  const agentCount = roster.reduce((count, user) => count + user.agents.length, 0);
  const myName = localStorage.getItem("agentsassemble.name") || defaultLobbyName("mine");
  return `
    <aside class="lobby-roster" aria-label="로비 참여자">
      <div class="roster-head">
        <strong>참여자</strong>
        <span>${participantCount}명 · 에이전트 ${agentCount}</span>
      </div>
      <label class="my-name-editor">
        <span>내 이름</span>
        <input id="lobby-my-name" maxlength="32" value="${escapeHtml(myName)}" />
      </label>
      ${
        roster.length
          ? roster.map(renderRosterUser).join("")
          : '<p class="roster-empty">아직 관찰된 참여자가 없습니다.</p>'
      }
      ${renderLiveAgentConnections()}
      ${renderApprovedBindings(state.payload?.meeting)}
      ${renderCodexSessionInvite(state.payload?.meeting)}
    </aside>
  `;
}

function renderLiveAgentConnections() {
  const agents = state.liveAgents || [];
  const counts = liveAgentStatusCounts(agents);
  const status = state.liveAgentStatus;
  return `
    <section class="live-agent-connections" aria-label="살아있는 에이전트">
      <div class="roster-head">
        <strong>살아있는 에이전트</strong>
        <span>${counts.live} live · ${counts.error} error · ${counts.stale} stale · ${counts.total} connected</span>
      </div>
      ${renderLiveAgentHealthStrip(counts)}
      <div class="live-agent-list">
        ${
          agents.length
            ? agents.map(renderLiveAgentCard).join("")
            : '<p class="roster-empty">아직 접속 등록된 에이전트가 없습니다.</p>'
        }
      </div>
      ${renderLiveAgentProcessControls()}
      <form id="live-agent-form" class="live-agent-form">
        <input id="live-agent-id" maxlength="64" placeholder="agent id" required />
        <input id="live-agent-display-name" maxlength="64" placeholder="표시 이름" />
        <select id="live-agent-provider-kind">
          ${renderLiveAgentProviderOptions()}
        </select>
        <select id="live-agent-connection-kind">
          <option value="local_cli">Local CLI</option>
          <option value="live_session">Live session</option>
          <option value="terminal_session">Terminal session</option>
          <option value="self_service">Self-service</option>
          <option value="remote_bridge">Remote bridge</option>
          <option value="codex_resume">Codex resume</option>
          <option value="manual">Manual</option>
        </select>
        <button type="submit" ${state.liveAgentsLoading ? "disabled" : ""}>접속 등록</button>
        <button type="button" id="live-agent-join-brief" ${state.liveAgentJoinBriefRunning ? "disabled" : ""}>초대 패킷</button>
        <button type="button" id="live-agent-refresh">갱신</button>
      </form>
      ${renderLiveAgentJoinBrief(state.liveAgentJoinBrief)}
      ${status ? `<p class="live-agent-status" data-tone="${escapeHtml(status.tone || "info")}">${escapeHtml(status.message)}</p>` : ""}
    </section>
  `;
}

function renderLiveAgentJoinBrief(brief) {
  if (!brief || typeof brief !== "object") return "";
  const agent = brief.agent && typeof brief.agent === "object" ? brief.agent : {};
  const agentId = String(agent.agent_id || "external-agent");
  const publicBrief = {
    packet_kind: brief.packet_kind || "",
    agent: brief.agent || {},
    entry_contract: brief.entry_contract || {},
    execution_contract: brief.execution_contract || {},
    commands: brief.commands || {},
    templates: brief.templates || {},
    mcp: brief.mcp || {},
    instructions: brief.instructions || [],
    env: brief.env || {},
    safety: brief.safety || {},
  };
  return `
    <section class="live-agent-join-brief" aria-label="External live-agent join brief">
      <div>
        <strong>${escapeHtml(agentId)} join brief</strong>
        <span>register once, then wait-next loop</span>
      </div>
      <pre>${escapeHtml(JSON.stringify(publicBrief, null, 2))}</pre>
    </section>
  `;
}

function renderLiveAgentProcessControls() {
  const groups = state.liveAgentProcesses || [];
  const counts = processGroupStatusCounts(groups);
  const status = state.liveAgentProcessStatus;
  const processActionsDisabled = state.liveAgentProcessesLoading || liveAgentProcessActionBusy();
  const currentMeeting = state.payload?.meeting || {};
  const defaultMeetingId = currentMeeting.meeting_id || "";
  const defaultRoundId = defaultOfficialRoundId(currentMeeting) || "round_1";
  const defaultFlowTopic = currentMeeting.display_topic || currentMeeting.topic || currentMeeting.question || "";
  return `
    <section class="live-agent-processes" aria-label="상주 실행">
      <div class="roster-head">
        <strong>상주 실행</strong>
        <span>${counts.running} running · ${counts.restarting} restarting · ${counts.error} error · ${counts.total} groups</span>
      </div>
      ${renderLiveAgentRuntimeHealth(state.liveAgentHealth, state.liveAgentHealthLoading)}
      ${renderProcessGroupHealthStrip(counts)}
      <form id="live-agent-process-form" class="live-agent-process-form">
        <section class="live-agent-waiting-room" aria-label="대기실 기본 흐름">
          <div class="live-agent-basic-controls">
            <label class="live-agent-meeting-field">
              <span>회의 ID</span>
              <input id="live-agent-session-meeting-id" maxlength="128" placeholder="meeting id" value="${escapeHtml(defaultMeetingId)}" data-default-value="${escapeHtml(defaultMeetingId)}" />
            </label>
            <div class="live-agent-flow-panel">
              <strong>Play Mode 자유토론</strong>
              <span class="live-agent-flow-status" aria-live="polite">${escapeHtml(liveAgentFlowStatusLabel(state.liveAgentFlow))}</span>
              <input id="live-agent-flow-topic" maxlength="240" value="${escapeHtml(defaultFlowTopic)}" aria-label="play mode flow topic" />
              <input id="live-agent-flow-duration" type="number" min="1" max="3600" step="1" value="180" aria-label="play mode flow duration seconds" />
              <button type="button" id="live-agent-flow-start" ${processActionsDisabled ? "disabled" : ""}>자유토론</button>
              <button type="button" id="live-agent-flow-stop" ${processActionsDisabled ? "disabled" : ""}>토론중지</button>
              ${renderLiveAgentFlowDiagnostics(state.liveAgentFlow)}
            </div>
          </div>
        </section>
        <details class="live-agent-advanced-controls">
          <summary>고급 운영</summary>
          <div class="live-agent-advanced-grid">
            <input id="live-agent-process-config" maxlength="240" value="configs/live-agents.start-session.example.json" />
            <input id="live-agent-process-group" maxlength="64" placeholder="group id" />
            <input id="live-agent-session-council-config" maxlength="240" value="configs/demo-council.json" />
            <input id="live-agent-session-agent-config" maxlength="240" value="configs/agents.start-session.example.json" />
            <input id="live-agent-session-connect-timeout" type="number" min="0" max="120" step="1" value="5" aria-label="session connect timeout seconds" />
            <input id="live-agent-round-id" maxlength="128" value="${escapeHtml(defaultRoundId)}" data-default-value="${escapeHtml(defaultRoundId)}" aria-label="official round id" />
            <input id="live-agent-round-timeout" type="number" min="0" max="600" step="1" value="30" aria-label="official round timeout seconds" />
            <input id="live-agent-round-max-rounds" type="number" min="1" max="8" step="1" value="8" aria-label="maximum remaining official rounds" />
            <label class="live-agent-process-options">
              <input id="live-agent-round-stop-on-timeout" type="checkbox" ${processActionsDisabled ? "disabled" : ""} />
              <span>timeout stop</span>
            </label>
        <label class="live-agent-process-options">
          <input id="live-agent-session-run-remaining-rounds" type="checkbox" ${processActionsDisabled ? "disabled" : ""} />
          <span>세션 후 남은라운드</span>
        </label>
        <label class="live-agent-process-options">
          <input id="live-agent-session-probe-bound-agents" type="checkbox" ${processActionsDisabled ? "disabled" : ""} />
          <span>응답검증</span>
        </label>
        <input id="live-agent-session-probe-timeout" type="number" min="0" max="240" step="0.5" value="12" aria-label="session reply probe timeout seconds" />
        <label class="live-agent-process-options">
          <input id="live-agent-process-auto-restart" type="checkbox" />
          <span>auto restart</span>
        </label>
        <input id="live-agent-process-max-restarts" type="number" min="0" max="99" value="3" aria-label="max restarts" />
        <input id="live-agent-process-restart-backoff" type="number" min="0" max="3600" step="1" value="5" aria-label="restart backoff seconds" />
        <input id="live-agent-process-stale-restart-after" type="number" min="0" max="86400" step="1" value="0" aria-label="stale restart after seconds" />
        <button type="submit" id="live-agent-process-start" ${processActionsDisabled ? "disabled" : ""}>시작</button>
        <button type="button" id="live-agent-process-stop-running" ${processActionsDisabled ? "disabled" : ""}>실행중지</button>
        <button type="button" id="live-agent-session-start" ${processActionsDisabled ? "disabled" : ""}>세션시작</button>
        <button type="button" id="live-agent-session-ensure" ${processActionsDisabled ? "disabled" : ""}>세션보장</button>
        <button type="button" id="live-agent-session-run-ensure" ${processActionsDisabled ? "disabled" : ""}>상주보장</button>
        <button type="button" id="live-agent-session-resume" ${processActionsDisabled ? "disabled" : ""}>세션재개</button>
        <button type="button" id="live-agent-session-restart" ${processActionsDisabled ? "disabled" : ""}>세션재시작</button>
        <button type="button" id="live-agent-session-recover" ${processActionsDisabled ? "disabled" : ""}>세션복구</button>
        <button type="button" id="live-agent-session-check" ${processActionsDisabled ? "disabled" : ""}>세션점검</button>
        <button type="button" id="live-agent-session-stop" ${processActionsDisabled ? "disabled" : ""}>세션중지</button>
        <button type="button" id="live-agent-call-round" ${processActionsDisabled ? "disabled" : ""}>라운드호출</button>
        <button type="button" id="live-agent-call-remaining-rounds" ${processActionsDisabled ? "disabled" : ""}>남은라운드</button>
        <input id="live-agent-review-checkpoint-message" data-live-agent-review-checkpoint-input maxlength="240" value="Review this resident slice before commit." aria-label="review checkpoint message" />
        <input id="live-agent-review-checkpoint-id" data-live-agent-review-checkpoint-input maxlength="128" placeholder="checkpoint id" aria-label="review checkpoint id" />
        <input id="live-agent-review-checkpoint-timeout" data-live-agent-review-checkpoint-input type="number" min="0" max="600" step="1" value="30" aria-label="review checkpoint timeout seconds" />
        <button type="button" id="live-agent-review-checkpoint" ${processActionsDisabled ? "disabled" : ""}>리뷰요청</button>
        <label class="live-agent-process-options">
          <input id="live-agent-discovery-session-bundle" type="checkbox" checked ${processActionsDisabled ? "disabled" : ""} />
          <span>세션번들</span>
        </label>
        <label class="live-agent-process-options">
          <input id="live-agent-auto-join-real-provider-approval" type="checkbox" ${processActionsDisabled ? "disabled" : ""} />
          <span>실사용 CLI 승인</span>
        </label>
        <button type="button" id="live-agent-discover" ${processActionsDisabled ? "disabled" : ""}>CLI발견</button>
        <button type="button" id="live-agent-auto-join" ${processActionsDisabled ? "disabled" : ""}>자동입장</button>
        <button type="button" id="live-agent-preflight-check" ${processActionsDisabled ? "disabled" : ""}>예비점검</button>
        <button type="button" id="live-agent-process-smoke" ${processActionsDisabled ? "disabled" : ""}>진단</button>
        <button type="button" id="live-agent-official-round-smoke" ${processActionsDisabled ? "disabled" : ""}>공식진단</button>
        <button type="button" id="live-agent-session-smoke" ${processActionsDisabled ? "disabled" : ""}>세션진단</button>
        <input id="live-agent-session-smoke-soak-cycles" type="number" min="0" max="5" step="1" value="0" aria-label="session smoke soak cycles" />
        <input id="live-agent-session-smoke-soak-interval" type="number" min="0" max="60" step="0.5" value="0" aria-label="session smoke soak interval seconds" />
        <label class="live-agent-process-options">
          <input id="live-agent-readiness-official-round" type="checkbox" ${processActionsDisabled ? "disabled" : ""} />
          <span>공식 포함</span>
        </label>
        <label class="live-agent-process-options">
          <input id="live-agent-readiness-session-smoke" type="checkbox" ${processActionsDisabled ? "disabled" : ""} />
          <span>세션 포함</span>
        </label>
        <button type="button" id="live-agent-readiness-check" ${processActionsDisabled ? "disabled" : ""}>점검</button>
        <button type="button" id="live-agent-process-refresh">상태</button>
          </div>
        </details>
      </form>
      ${renderLiveAgentDiscoveryReport(state.liveAgentDiscoveryReport)}
      <div class="live-agent-process-list">
        ${
          groups.length
            ? groups.map(renderLiveAgentProcessCard).join("")
            : '<p class="roster-empty">실행 중인 상주 그룹이 없습니다.</p>'
        }
      </div>
      ${renderLiveAgentProcessEvents()}
      ${renderLiveAgentSessionRuns()}
      ${renderLiveAgentOperations()}
      ${status ? `<p class="live-agent-status" data-tone="${escapeHtml(status.tone || "info")}">${escapeHtml(status.message)}</p>` : ""}
    </section>
  `;
}

function liveAgentProcessActionBusy() {
  return state.liveAgentProcessStartRunning || state.liveAgentSessionStartRunning || state.liveAgentSessionRestartRunning || state.liveAgentSessionRecoverRunning || state.liveAgentSessionCheckRunning || state.liveAgentSessionStopRunning || state.liveAgentFlowStartRunning || state.liveAgentFlowStopRunning || state.liveAgentReviewCheckpointRunning || state.liveAgentRoundCallRunning || state.liveAgentPreflightRunning || state.liveAgentSmokeRunning || state.liveAgentOfficialRoundSmokeRunning || state.liveAgentSessionSmokeRunning || state.liveAgentReadinessRunning || state.liveAgentDiscoveryRunning || state.liveAgentAutoJoinRunning || Boolean(state.liveAgentProcessRowActionRunning) || state.liveAgentProcessBulkStopRunning || Boolean(state.liveAgentSessionRunRetryNowRunning) || Boolean(state.liveAgentSessionRunActionRunning);
}

function liveAgentFlowStatusLabel(flow) {
  if (!flow || typeof flow !== "object" || !flow.status || flow.status === "idle") return "대기";
  const status = String(flow.status || "idle");
  const parts = [liveAgentFlowStatusText(status)];
  if (status === "running" && Number.isFinite(Number(flow.remaining_seconds))) {
    parts.push(`${liveAgentFlowClockLabel(flow.remaining_seconds)} 남음`);
  }
  parts.push(`참여 ${Math.max(0, Number(flow.agent_count || 0))}명`);
  return parts.join(" · ");
}

function renderLiveAgentFlowDiagnostics(flow) {
  if (!flow || typeof flow !== "object" || !flow.status || flow.status === "idle") return "";
  const details = liveAgentFlowDiagnosticParts(flow);
  if (!details.length) return "";
  return `
    <details class="live-agent-flow-diagnostics">
      <summary>상태 자세히</summary>
      <span>${escapeHtml(details.join(" · "))}</span>
    </details>
  `;
}

function liveAgentFlowDiagnosticParts(flow) {
  const turns = Math.max(0, Number(flow.total_turns || 0));
  const details = [`총 ${turns}마디`];
  const lastActivity = liveAgentFlowElapsedSinceLabel(flow.last_activity_at);
  details.push(lastActivity && turns > 0 ? `마지막 ${lastActivity} 전` : "아직 발언 없음");
  return details;
}

function liveAgentFlowStatusText(status) {
  if (status === "running") return "진행중";
  if (status === "finished") return "종료";
  if (status === "stopped") return "중지";
  if (status === "error") return "오류";
  return "대기";
}

function liveAgentFlowClockLabel(value) {
  const totalSeconds = Math.max(0, Math.ceil(Number(value) || 0));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (hours > 0) {
    return `${hours}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
  }
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

function liveAgentFlowElapsedSinceLabel(value) {
  const parsed = Date.parse(String(value || ""));
  if (!Number.isFinite(parsed)) return "";
  const elapsedSeconds = Math.max(0, Math.round((Date.now() - parsed) / 1000));
  if (elapsedSeconds < 60) return `${elapsedSeconds}초`;
  const minutes = Math.floor(elapsedSeconds / 60);
  if (minutes < 60) return `${minutes}분`;
  const hours = Math.floor(minutes / 60);
  const remainingMinutes = minutes % 60;
  return remainingMinutes ? `${hours}시간 ${remainingMinutes}분` : `${hours}시간`;
}

function defaultOfficialRoundId(meeting) {
  const rounds = Array.isArray(meeting?.meeting_template?.rounds)
    ? meeting.meeting_template.rounds.filter((round) => round?.id)
    : [];
  if (!rounds.length) return "";
  const completedRoundIds = new Set(
    (meeting?.debate_rounds || [])
      .filter((round) => round?.status === "answered")
      .map((round) => String(round.id || round.round || ""))
      .filter(Boolean)
  );
  const nextRound = rounds.find((round) => !completedRoundIds.has(String(round.id || ""))) || rounds[0];
  return String(nextRound.id || "");
}

function liveAgentStatusCounts(agents) {
  const counts = { online: 0, working: 0, error: 0, stale: 0, offline: 0, live: 0, total: agents.length };
  for (const agent of agents) {
    const status = String(agent.status || "offline");
    if (status === "online" || status === "working" || status === "error" || status === "stale" || status === "offline") {
      counts[status] += 1;
    } else {
      counts.offline += 1;
    }
  }
  counts.live = counts.online + counts.working;
  return counts;
}

function processGroupStatusCounts(groups) {
  const counts = { running: 0, restarting: 0, error: 0, unknown: 0, stopped: 0, total: groups.length };
  for (const group of groups) {
    const status = String(group.status || "unknown");
    if (status === "running" || status === "restarting" || status === "error" || status === "unknown" || status === "stopped") {
      counts[status] += 1;
    } else {
      counts.unknown += 1;
    }
  }
  return counts;
}

function renderLiveAgentHealthStrip(counts) {
  return `
    <div class="live-agent-health-strip" aria-label="Live agent status summary">
      ${renderHealthPill("online", "online", counts.online)}
      ${renderHealthPill("working", "working", counts.working)}
      ${renderHealthPill("error", "error", counts.error)}
      ${renderHealthPill("stale", "stale", counts.stale)}
      ${renderHealthPill("offline", "offline", counts.offline)}
    </div>
  `;
}

function renderProcessGroupHealthStrip(counts) {
  return `
    <div class="live-agent-process-health-strip" aria-label="Live agent process status summary">
      ${renderHealthPill("running", "running", counts.running)}
      ${renderHealthPill("restarting", "restarting", counts.restarting)}
      ${renderHealthPill("error", "error", counts.error)}
      ${renderHealthPill("unknown", "unknown", counts.unknown)}
      ${renderHealthPill("stopped", "stopped", counts.stopped)}
    </div>
  `;
}

function renderHealthPill(status, label, count) {
  return `<span class="live-agent-health-pill live-agent-health-${escapeHtml(status)}"><strong>${escapeHtml(count)}</strong>${escapeHtml(label)}</span>`;
}

function renderLiveAgentRuntimeHealth(health, loading) {
  if (!health || typeof health !== "object") {
    const label = loading ? "runtime health loading" : "runtime health not loaded";
    return `<p class="live-agent-runtime-health" data-tone="info">${label}</p>`;
  }
  const status = String(health.status || "unknown");
  const agents = health.agents && typeof health.agents === "object" ? health.agents : {};
  const processes = health.processes && typeof health.processes === "object" ? health.processes : {};
  const processMonitor = health.process_monitor && typeof health.process_monitor === "object" ? health.process_monitor : {};
  const connections = health.connections && typeof health.connections === "object" ? health.connections : {};
  const sandboxEnforcement = health.sandbox_enforcement && typeof health.sandbox_enforcement === "object" ? health.sandbox_enforcement : {};
  const sessions = health.sessions && typeof health.sessions === "object" ? health.sessions : {};
  const observations = health.observations && typeof health.observations === "object" ? health.observations : {};
  const admission = health.admission && typeof health.admission === "object" ? health.admission : {};
  const sharedMemory = health.shared_memory && typeof health.shared_memory === "object" ? health.shared_memory : {};
  const sessionRuns = health.session_runs && typeof health.session_runs === "object" ? health.session_runs : {};
  const sessionRunMonitor = health.session_run_monitor && typeof health.session_run_monitor === "object" ? health.session_run_monitor : {};
  const processCounts = processes.counts && typeof processes.counts === "object" ? processes.counts : {};
  const agentLive = Math.max(0, Number(agents.live || 0));
  const agentTotal = Math.max(0, Number(agents.total || 0));
  const runningProcesses = Math.max(0, Number(processCounts.running || 0));
  const processTotal = Math.max(0, Number(processes.total || 0));
  const connected = Math.max(0, Number(connections.connected || 0));
  const expected = Math.max(0, Number(connections.expected || 0));
  const readySessions = Math.max(0, Number(sessions.ready || 0));
  const sessionTotal = Math.max(0, Number(sessions.total || 0));
  const activeSessionRuns = Math.max(0, Number(sessionRuns.active || 0));
  const sessionRunTotal = Math.max(0, Number(sessionRuns.total || 0));
  const attentionCount = liveAgentHealthAttentionCount(health);
  const processMonitorSummary = liveAgentHealthProcessMonitorSummary(processMonitor);
  const sessionAttention = liveAgentHealthAttentionSummary(sessions.attention, "session attention");
  const observationSummary = liveAgentHealthObservationSummary(observations);
  const observationAttention = liveAgentHealthAttentionSummary(observations.attention, "observation attention");
  const sandboxSummary = liveAgentHealthSandboxSummary(sandboxEnforcement);
  const sandboxAttention = liveAgentHealthAttentionSummary(sandboxEnforcement.attention, "sandbox attention");
  const admissionSummary = liveAgentHealthAdmissionSummary(admission);
  const admissionAttention = liveAgentHealthAttentionSummary(admission.attention, "admission attention");
  const sharedMemorySummary = liveAgentHealthSharedMemorySummary(sharedMemory);
  const sharedMemoryAttention = liveAgentHealthAttentionSummary(sharedMemory.attention, "shared-memory attention");
  const sessionRunAttention = liveAgentHealthAttentionSummary(sessionRuns.attention, "session-run attention");
  const sessionRunRetry = liveAgentHealthSessionRunRetrySummary(sessionRuns.items);
  const sessionRunMonitorSummary = liveAgentHealthSessionRunMonitorSummary(sessionRunMonitor);
  const tone = status === "ok" ? "success" : status === "degraded" ? "warning" : "error";
  return (
    `<p class="live-agent-runtime-health" data-tone="${escapeHtml(tone)}">` +
    `runtime health ${escapeHtml(status)} · ` +
    `agents ${escapeHtml(`${agentLive}/${agentTotal}`)} live · ` +
    `processes ${escapeHtml(`${runningProcesses}/${processTotal}`)} running · ` +
    `connections ${escapeHtml(`${connected}/${expected}`)} connected · ` +
    `sessions ${escapeHtml(`${readySessions}/${sessionTotal}`)} ready · ` +
    `session-runs ${escapeHtml(`${activeSessionRuns}/${sessionRunTotal}`)} active · ` +
    `attention ${escapeHtml(attentionCount)}` +
    (processMonitorSummary ? `<br><small>${escapeHtml(processMonitorSummary)}</small>` : "") +
    (sessionAttention ? `<br><small>${escapeHtml(sessionAttention)}</small>` : "") +
    (observationSummary ? `<br><small>${escapeHtml(observationSummary)}</small>` : "") +
    (observationAttention ? `<br><small>${escapeHtml(observationAttention)}</small>` : "") +
    (sandboxSummary ? `<br><small>${escapeHtml(sandboxSummary)}</small>` : "") +
    (sandboxAttention ? `<br><small>${escapeHtml(sandboxAttention)}</small>` : "") +
    (admissionSummary ? `<br><small>${escapeHtml(admissionSummary)}</small>` : "") +
    (admissionAttention ? `<br><small>${escapeHtml(admissionAttention)}</small>` : "") +
    (sharedMemorySummary ? `<br><small>${escapeHtml(sharedMemorySummary)}</small>` : "") +
    (sharedMemoryAttention ? `<br><small>${escapeHtml(sharedMemoryAttention)}</small>` : "") +
    (sessionRunAttention ? `<br><small>${escapeHtml(sessionRunAttention)}</small>` : "") +
    (sessionRunRetry ? `<br><small>${escapeHtml(sessionRunRetry)}</small>` : "") +
    (sessionRunMonitorSummary ? `<br><small>${escapeHtml(sessionRunMonitorSummary)}</small>` : "") +
    "</p>" +
    renderLiveAgentSessionReadiness(sessions)
  );
}

function renderLiveAgentSessionReadiness(sessions) {
  const items = sessions && typeof sessions === "object" && Array.isArray(sessions.items) ? sessions.items : [];
  if (!items.length) return "";
  return `
    <div class="live-agent-session-readiness" aria-label="상주 세션 readiness">
      ${items.map(renderLiveAgentSessionReadinessRow).join("")}
    </div>
  `;
}

function renderLiveAgentSessionReadinessRow(session) {
  const status = String(session.status || "unknown");
  const meetingId = String(session.meeting_id || "-");
  const groupId = String(session.group_id || "-");
  const processStatus = String(session.process_status || "unknown");
  const expected = Math.max(0, Number(session.expected || 0));
  const connected = Math.max(0, Number(session.connected || 0));
  const details = [
    `process ${processStatus}`,
    `connected ${connected}/${expected}`,
    liveAgentSessionAttentionLabel("ownership", session.ownership_attention),
    liveAgentSessionAttentionLabel("process", session.process_attention),
    liveAgentSessionAttentionLabel("connection", session.connection_attention),
    liveAgentSessionProcessReasonLabel(session.process_reason),
  ]
    .filter(Boolean)
    .join(" · ");
  return `
    <article class="live-agent-session-row live-agent-session-${escapeHtml(status)}">
      <div>
        <strong>${escapeHtml(meetingId)}</strong>
        <span>${escapeHtml(groupId)}</span>
        <small>${escapeHtml(details)}</small>
      </div>
      <em>${escapeHtml(status)}</em>
    </article>
  `;
}

function liveAgentSessionAttentionLabel(label, value) {
  if (!Array.isArray(value) || !value.length) return "";
  const clean = value.map((item) => String(item || "").trim()).filter(Boolean).slice(0, 5);
  return clean.length ? `${label} ${clean.join(", ")}` : "";
}

function liveAgentSessionProcessReasonLabel(reason) {
  if (!reason || typeof reason !== "object") return "";
  const eventType = String(reason.event_type || "").trim();
  const text = String(reason.reason || "").trim();
  if (!eventType && !text) return "";
  return `reason ${[eventType, text].filter(Boolean).join(" ")}`;
}

function renderLiveAgentDiscoveryReport(report) {
  if (!report || typeof report !== "object") return "";
  const discoveries = Array.isArray(report.discoveries) ? report.discoveries : [];
  if (!discoveries.length) return "";
  const included = discoveries.filter((item) => item && item.included).length;
  const found = discoveries.filter((item) => item && item.available).length;
  return `
    <section class="live-agent-discovery-report" aria-label="Live agent CLI discovery report">
      <div>
        <strong>CLI discovery</strong>
        <span>included ${escapeHtml(`${included}/${discoveries.length}`)} · found ${escapeHtml(found)}</span>
      </div>
      ${discoveries.map(renderLiveAgentDiscoveryRow).join("")}
      ${renderLiveAgentDiscoverySessionBundle(report)}
      ${renderLiveAgentDiscoveryNextCommands(report)}
    </section>
  `;
}

function renderLiveAgentDiscoverySessionBundle(report) {
  const sessionBundle = report?.session_bundle && typeof report.session_bundle === "object" ? report.session_bundle : null;
  if (!sessionBundle) return "";
  const parts = [
    sessionBundle.group_id ? `group ${sessionBundle.group_id}` : "",
    sessionBundle.council_config_path ? `council ${sessionBundle.council_config_path}` : "",
    sessionBundle.agent_config_path ? `agents ${sessionBundle.agent_config_path}` : "",
  ].filter(Boolean);
  if (!parts.length) return "";
  return `<small>${escapeHtml(parts.join(" · "))}</small>`;
}

function renderLiveAgentDiscoveryNextCommands(report) {
  const nextCommands = report?.next_commands && typeof report.next_commands === "object" ? report.next_commands : {};
  const entries = [
    ["ensure_session", nextCommands.ensure_session],
    ...Object.entries(nextCommands).filter(([name]) => name !== "ensure_session"),
  ].filter(([, command]) => Array.isArray(command) && command.length);
  if (!entries.length) return "";
  return entries
    .map(([name, command]) => `<small>${escapeHtml(name)}: ${escapeHtml(command.join(" "))}</small>`)
    .join("");
}

function renderLiveAgentDiscoveryRow(discovery) {
  const command = String(discovery?.command || "unknown");
  const agentId = String(discovery?.agent_id || "").trim();
  const providerKind = String(discovery?.provider_kind || "unknown");
  const entryMode = String(discovery?.entry_mode || discovery?.connection_kind || "");
  const entryStatus = String(discovery?.entry_status || "");
  const joinSemantics = String(discovery?.join_semantics || "");
  const contextDurability = String(discovery?.context_durability || "");
  const evidenceBasis = String(discovery?.evidence_basis || "");
  const operatorAction = String(discovery?.operator_action || "");
  const approval = discovery?.requires_approval ? "approval required" : "";
  const approvalStatus = String(discovery?.approval_status || "");
  const safetyNote = String(discovery?.safety_note || "");
  const reason = String(discovery?.reason || "");
  const status = discovery?.included ? "included" : discovery?.available ? "skipped" : "missing";
  const detail = [providerKind, agentId, entryMode, joinSemantics, contextDurability, evidenceBasis, entryStatus || reason, operatorAction, approvalStatus, approval, safetyNote]
    .filter(Boolean)
    .join(" · ");
  const approvalAgentId = liveAgentDiscoverySafeApprovalAgentId(agentId) ? agentId : "";
  const approvalControl = discovery?.included && discovery?.requires_approval && approvalAgentId
    ? `
      <label class="live-agent-process-options">
        <input type="checkbox" data-live-agent-discovery-approve-agent="${escapeHtml(approvalAgentId)}" />
        <span>approve</span>
      </label>
    `
    : "";
  return `
    <article class="live-agent-discovery-row live-agent-discovery-${escapeHtml(status)}">
      <div>
        <strong>${escapeHtml(command)}</strong>
        <small>${escapeHtml(detail)}</small>
      </div>
      ${approvalControl}
      <em>${escapeHtml(status)}</em>
    </article>
  `;
}

function liveAgentHealthAttentionCount(health) {
  const sections = [health?.agents, health?.processes, health?.process_monitor, health?.connections, health?.sandbox_enforcement, health?.sessions, health?.observations, health?.shared_memory, health?.session_runs, health?.session_run_monitor];
  return sections.reduce((count, section) => {
    const attention = section && typeof section === "object" && Array.isArray(section.attention) ? section.attention : [];
    return count + attention.length;
  }, 0);
}

function liveAgentHealthSandboxSummary(value) {
  if (!value || typeof value !== "object" || !value.counts || typeof value.counts !== "object") return "";
  const counts = value.counts;
  const advisory = Math.max(0, Number(counts.advisory || 0));
  const codexReadonly = Math.max(0, Number(counts.codex_readonly || 0));
  const osSandboxed = Math.max(0, Number(counts.os_sandboxed || 0));
  const unknown = Math.max(0, Number(counts.unknown || 0));
  if (!advisory && !codexReadonly && !osSandboxed && !unknown) return "";
  return (
    `sandbox advisory ${Math.floor(advisory)} · ` +
    `codex_readonly ${Math.floor(codexReadonly)} · ` +
    `os_sandboxed ${Math.floor(osSandboxed)} · ` +
    `unknown ${Math.floor(unknown)}`
  );
}

function liveAgentHealthProcessMonitorSummary(value) {
  if (!value || typeof value !== "object" || !("running" in value || "last_status" in value || "last_tick_at")) return "";
  const parts = [`process monitor ${value.running === true ? "running" : "stopped"}`];
  const intervalSeconds = Number(value.interval_seconds || 0);
  const lastStatus = String(value.last_status || "").trim();
  const lastGroupCount = Number(value.last_group_count || 0);
  const lastTickAt = String(value.last_tick_at || "").trim();
  const lastErrorType = String(value.last_error_type || "").trim();
  const intervalLabel = liveAgentHealthSecondsLabel(intervalSeconds);
  if (intervalLabel) parts.push(`interval ${intervalLabel}`);
  if (/^[a-z_]{1,32}$/.test(lastStatus)) parts.push(`last ${lastStatus}`);
  if (Number.isFinite(lastGroupCount)) parts.push(`groups ${Math.max(0, Math.floor(lastGroupCount))}`);
  if (/^[0-9T:+.\-Z]{1,64}$/.test(lastTickAt)) parts.push(`last tick ${lastTickAt}`);
  if (/^[A-Za-z_][A-Za-z0-9_.]{0,79}$/.test(lastErrorType)) parts.push(`error ${lastErrorType}`);
  return parts.join(" · ");
}

function liveAgentHealthAttentionSummary(value, label) {
  if (!Array.isArray(value) || value.length === 0) return "";
  const cleaned = value.map((item) => String(item || "").trim()).filter(Boolean).slice(0, 3);
  if (!cleaned.length) return "";
  const remaining = Math.max(0, value.length - cleaned.length);
  const suffix = remaining > 0 ? ` +${remaining} more` : "";
  return `${label} ${cleaned.join(", ")}${suffix}`;
}

function liveAgentHealthObservationSummary(value) {
  if (!value || typeof value !== "object" || !("ready_agent_count" in value || "lobby_behind_count" in value || "live_behind_count" in value)) return "";
  const readyAgents = Math.max(0, Number(value.ready_agent_count || 0));
  const lobbyBehind = Math.max(0, Number(value.lobby_behind_count || 0));
  const liveBehind = Math.max(0, Number(value.live_behind_count || 0));
  const errors = Math.max(0, Number(value.error_count || 0));
  return `observations ${Math.floor(readyAgents)} ready agents · lobby behind ${Math.floor(lobbyBehind)} · live behind ${Math.floor(liveBehind)} · errors ${Math.floor(errors)}`;
}

function liveAgentHealthAdmissionSummary(value) {
  if (!value || typeof value !== "object" || !("total" in value || "host_approved" in value || "unapproved" in value)) return "";
  const counts = value.counts && typeof value.counts === "object" ? value.counts : {};
  const total = Math.max(0, Number(value.total || 0));
  const approved = Math.max(0, Number(value.host_approved || 0));
  const unapproved = Math.max(0, Number(value.unapproved || 0));
  const bound = Math.max(0, Number(counts.bound_to_meeting || 0));
  const conflicts = Math.max(0, Number(counts.binding_conflict || 0));
  const meetingLobby = Math.max(0, Number(counts.meeting_lobby_only || 0));
  return (
    `admission ${Math.floor(approved)}/${Math.floor(total)} host-approved · ` +
    `unapproved ${Math.floor(unapproved)} · bound ${Math.floor(bound)} · ` +
    `binding conflict ${Math.floor(conflicts)} · meeting lobby ${Math.floor(meetingLobby)}`
  );
}

function liveAgentHealthSharedMemorySummary(value) {
  if (!value || typeof value !== "object") return "";
  const officialEvents = Math.max(0, Number(value.official_event_count || 0));
  const readySessions = Math.max(0, Number(value.ready_sessions || 0));
  const withMemory = Math.max(0, Number(value.with_memory || 0));
  if (!officialEvents && !withMemory) return "";
  const questions = Math.max(0, Number(value.open_question_count || 0));
  const actions = Math.max(0, Number(value.action_item_count || 0));
  const latest = String(value.last_official_event_id || "").trim();
  return (
    `shared memory ${Math.floor(officialEvents)} official events · ` +
    `${Math.floor(withMemory)}/${Math.floor(readySessions)} ready sessions · ` +
    `questions ${Math.floor(questions)} · actions ${Math.floor(actions)}` +
    (latest ? ` · last ${latest}` : "")
  );
}

function liveAgentHealthSessionRunRetrySummary(value) {
  if (!Array.isArray(value) || value.length === 0) return "";
  const labels = [];
  for (const item of value) {
    if (!item || typeof item !== "object") continue;
    const parts = [];
    const failures = Number(item.reconcile_failure_count || 0);
    const backoffSeconds = Number(item.reconcile_backoff_seconds || 0);
    const nextReconcileAt = String(item.next_reconcile_at || "").trim();
    if (Number.isFinite(failures) && failures > 0) parts.push(`retry failures ${Math.floor(failures)}`);
    if (Number.isFinite(backoffSeconds) && backoffSeconds > 0) parts.push(`retry backoff ${Math.floor(backoffSeconds)}s`);
    if (/^[0-9T:+.\-Z]{1,64}$/.test(nextReconcileAt)) parts.push(`next retry ${nextReconcileAt}`);
    if (parts.length) labels.push(parts.join(" · "));
  }
  return labels.length ? `session-run retries ${labels.slice(0, 3).join(", ")}` : "";
}

function liveAgentHealthSessionRunMonitorSummary(value) {
  if (!value || typeof value !== "object" || !("running" in value || "last_status" in value || "last_tick_at" in value)) return "";
  const parts = [`session-run monitor ${value.running === true ? "running" : "stopped"}`];
  const intervalSeconds = Number(value.interval_seconds || 0);
  const lastStatus = String(value.last_status || "").trim();
  const lastResultCount = Number(value.last_result_count || 0);
  const lastTickAt = String(value.last_tick_at || "").trim();
  const lastErrorType = String(value.last_error_type || "").trim();
  const intervalLabel = liveAgentHealthSecondsLabel(intervalSeconds);
  if (intervalLabel) parts.push(`interval ${intervalLabel}`);
  if (/^[a-z_]{1,32}$/.test(lastStatus)) parts.push(`last ${lastStatus}`);
  if (Number.isFinite(lastResultCount)) parts.push(`results ${Math.max(0, Math.floor(lastResultCount))}`);
  if (/^[0-9T:+.\-Z]{1,64}$/.test(lastTickAt)) parts.push(`last tick ${lastTickAt}`);
  if (/^[A-Za-z_][A-Za-z0-9_.]{0,79}$/.test(lastErrorType)) parts.push(`error ${lastErrorType}`);
  return parts.join(" · ");
}

function liveAgentHealthSecondsLabel(value) {
  if (!Number.isFinite(value) || value <= 0) return "";
  return `${Number.isInteger(value) ? value : Number(value.toFixed(3))}s`;
}

function renderLiveAgentProcessCard(group) {
  const status = group.status || "unknown";
  const canStop = status === "running" || status === "restarting";
  const canRecover = status === "unknown" || status === "error";
  const actionDisabled = liveAgentProcessActionBusy();
  const actionDisabledAttribute = actionDisabled ? " disabled" : "";
  const logTail = group.log_tail == null ? "" : String(group.log_tail);
  const agentLabel = liveAgentProcessAgentsLabel(group);
  const connectionLabel = liveAgentProcessConnectionLabel(group);
  const eventLabel = liveAgentProcessEventLabel(group);
  const meetingLabel = liveAgentProcessMeetingLabel(group);
  return `
    <article class="live-agent-process-row live-agent-process-${escapeHtml(status)}">
      <div>
        <strong>${escapeHtml(group.group_id || "live-agents")}</strong>
        <span>${escapeHtml(group.config_path || "")}</span>
        <small>${escapeHtml(group.pid ? `pid ${group.pid}` : "pid 없음")} · ${escapeHtml(group.server || "")}</small>
        ${meetingLabel ? `<small class="live-agent-process-meeting">${escapeHtml(meetingLabel)}</small>` : ""}
        <small>${escapeHtml(liveAgentProcessRestartLabel(group))}</small>
        ${agentLabel ? `<small class="live-agent-process-agents">${escapeHtml(agentLabel)}</small>` : ""}
        ${connectionLabel ? `<small class="live-agent-process-connection">${escapeHtml(connectionLabel)}</small>` : ""}
        ${eventLabel ? `<small class="live-agent-process-event">${escapeHtml(eventLabel)}</small>` : ""}
      </div>
      <em>${escapeHtml(liveAgentProcessStatusLabel(status))}</em>
      ${
        canStop
          ? `<button type="button" data-live-agent-process-stop="${escapeHtml(group.group_id || "")}"${actionDisabledAttribute}>중지</button>`
          : canRecover
            ? `<button type="button" class="live-agent-process-recover" data-live-agent-process-recover="${escapeHtml(group.group_id || "")}"${actionDisabledAttribute}>복구</button>`
            : `<button type="button" class="live-agent-process-restart" data-live-agent-process-restart="${escapeHtml(group.group_id || "")}"${actionDisabledAttribute}>재시작</button>`
      }
      ${logTail ? `<pre class="live-agent-process-log">${escapeHtml(logTail)}</pre>` : ""}
    </article>
  `;
}

function renderLiveAgentOperations() {
  const operations = state.liveAgentOperations || [];
  return `
    <div class="live-agent-operation-list" aria-label="최근 상주 작업">
      <strong>최근 작업</strong>
      ${
        operations.length
          ? operations.slice(-6).reverse().map(renderLiveAgentOperation).join("")
          : '<p class="roster-empty">기록된 작업이 없습니다.</p>'
      }
    </div>
  `;
}

function renderLiveAgentSessionRuns() {
  const runs = state.liveAgentSessionRuns || [];
  return `
    <div class="live-agent-session-run-list" aria-label="상주 세션런">
      <strong>상주 세션런</strong>
      ${
        runs.length
          ? runs.slice(-6).reverse().map(renderLiveAgentSessionRun).join("")
          : '<p class="roster-empty">기록된 상주 세션런이 없습니다.</p>'
      }
    </div>
  `;
}

function renderLiveAgentSessionRun(run) {
  const status = String(run.status || "unknown");
  const runId = String(run.run_id || "-");
  const meetingId = String(run.meeting_id || "-");
  const groupId = String(run.group_id || "-");
  const activity = run.active === true ? "active" : "inactive";
  const readiness = liveAgentSessionRunReadinessPayload(run);
  const canRetry = liveAgentSessionRunCanRetry(run, readiness);
  const canPause = liveAgentSessionRunCanPause(run);
  const canResume = liveAgentSessionRunCanResume(run);
  const canStop = liveAgentSessionRunCanStop(run);
  const actionDisabled = liveAgentProcessActionBusy() ? " disabled" : "";
  const stateLabel = readiness ? `readiness ${String(readiness.status || "unknown")} · run ${status} · ${activity}` : `${status} · ${activity}`;
  const details = [
    `phase ${String(run.phase || status)}`,
    liveAgentSessionRunConnectionLabel(run, { stored: Boolean(readiness) }),
    liveAgentSessionRunReadinessLabel(readiness),
    run.reconcile_count ? `reconcile ${Math.max(0, Number(run.reconcile_count || 0))}` : "",
    liveAgentSessionRunRetryLabel(run),
    liveAgentSessionRunPausedLabel(run),
  ]
    .filter(Boolean)
    .join(" · ");
  return `
    <article class="live-agent-session-run-row live-agent-session-run-${escapeHtml(status)}">
      <div>
        <strong>${escapeHtml(runId)}</strong>
        <span>${escapeHtml(meetingId)} · ${escapeHtml(groupId)}</span>
        <small>${escapeHtml(details)}</small>
      </div>
      ${canPause ? `<button type="button" data-live-agent-session-run-pause="${escapeHtml(runId)}"${actionDisabled}>일시정지</button>` : ""}
      ${canResume ? `<button type="button" data-live-agent-session-run-resume="${escapeHtml(runId)}"${actionDisabled}>재개</button>` : ""}
      ${canRetry ? `<button type="button" data-live-agent-session-run-retry-now="${escapeHtml(runId)}"${actionDisabled}>재시도</button>` : ""}
      ${canStop ? `<button type="button" data-live-agent-session-run-stop="${escapeHtml(runId)}"${actionDisabled}>중지</button>` : ""}
      <em>${escapeHtml(stateLabel)}</em>
    </article>
  `;
}

function liveAgentSessionRunCanStop(run) {
  const status = String(run?.status || "unknown");
  const runId = String(run?.run_id || "").trim();
  return run?.active === true && Boolean(runId) && !["failed", "stopped"].includes(status);
}

function liveAgentSessionRunCanPause(run) {
  const status = String(run?.status || "unknown");
  const runId = String(run?.run_id || "").trim();
  return run?.active === true && Boolean(runId) && !["failed", "stopped", "paused"].includes(status);
}

function liveAgentSessionRunCanResume(run) {
  const status = String(run?.status || "unknown");
  const runId = String(run?.run_id || "").trim();
  return status === "paused" && Boolean(runId);
}

function liveAgentSessionRunCanRetry(run, readiness) {
  const status = String(run?.status || "unknown");
  const runId = String(run?.run_id || "").trim();
  if (run?.active !== true || !runId || ["failed", "stopped"].includes(status)) return false;
  if (status !== "ready") return true;
  const failures = Number(run?.reconcile_failure_count || 0);
  const backoffSeconds = Number(run?.reconcile_backoff_seconds || 0);
  const nextReconcileAt = String(run?.next_reconcile_at || "").trim();
  if ((Number.isFinite(failures) && failures > 0) || (Number.isFinite(backoffSeconds) && backoffSeconds > 0) || nextReconcileAt) {
    return true;
  }
  const readinessStatus = String(readiness?.status || "").trim();
  return Boolean(readinessStatus && readinessStatus !== "ready");
}

function liveAgentSessionRunConnectionLabel(run, options = {}) {
  const result = run?.result && typeof run.result === "object" ? run.result : {};
  const connection = result.connection && typeof result.connection === "object" ? result.connection : {};
  const expected = Number(connection.expected || 0);
  const connected = Number(connection.connected || 0);
  if (!Number.isFinite(expected) || !Number.isFinite(connected) || expected <= 0) return "";
  const label = options.stored ? "stored connected" : "connected";
  return `${label} ${Math.max(0, connected)}/${Math.max(0, expected)}`;
}

function liveAgentSessionRunReadinessPayload(run) {
  const readiness = run?.readiness && typeof run.readiness === "object" ? run.readiness : null;
  return readiness && !Array.isArray(readiness) ? readiness : null;
}

function liveAgentSessionRunReadinessLabel(readiness) {
  if (!readiness) return "";
  const expected = Number(readiness.expected || 0);
  const connected = Number(readiness.connected || 0);
  const parts = [];
  if (Number.isFinite(expected) && Number.isFinite(connected) && expected > 0) {
    parts.push(`current connected ${Math.max(0, connected)}/${Math.max(0, expected)}`);
  }
  parts.push(liveAgentSessionAttentionLabel("ownership", readiness.ownership_attention));
  parts.push(liveAgentSessionAttentionLabel("process", readiness.process_attention));
  parts.push(liveAgentSessionAttentionLabel("connection", readiness.connection_attention));
  parts.push(liveAgentSessionAttentionLabel("attention", readiness.attention));
  return parts.filter(Boolean).join(" · ");
}

function liveAgentSessionRunRetryLabel(run) {
  const labels = [];
  const failures = Number(run?.reconcile_failure_count || 0);
  const backoffSeconds = Number(run?.reconcile_backoff_seconds || 0);
  const nextReconcileAt = String(run?.next_reconcile_at || "").trim();
  if (Number.isFinite(failures) && failures > 0) labels.push(`retry failures ${Math.floor(failures)}`);
  if (Number.isFinite(backoffSeconds) && backoffSeconds > 0) labels.push(`retry backoff ${Math.floor(backoffSeconds)}s`);
  if (/^[0-9T:+.\-Z]{1,64}$/.test(nextReconcileAt)) labels.push(`next retry ${nextReconcileAt}`);
  return labels.join(" · ");
}

function liveAgentSessionRunPausedLabel(run) {
  const pausedStatus = String(run?.paused_status || "").trim();
  return pausedStatus ? `paused from ${pausedStatus}` : "";
}

function renderLiveAgentProcessEvents() {
  const events = state.liveAgentProcessEvents || [];
  const meta = state.liveAgentProcessEventsMeta;
  return `
    <div class="live-agent-lifecycle-list" aria-label="최근 프로세스 lifecycle">
      <strong>최근 lifecycle</strong>
      ${
        events.length
          ? events.slice().reverse().map(renderLiveAgentProcessEvent).join("")
          : '<p class="roster-empty">기록된 lifecycle 이벤트가 없습니다.</p>'
      }
      ${meta?.truncated ? `<small class="live-agent-lifecycle-meta">${escapeHtml(liveAgentProcessEventsTruncatedLabel(meta))}</small>` : ""}
    </div>
  `;
}

function renderLiveAgentProcessEvent(event) {
  const eventType = String(event.event_type || "updated");
  const status = String(event.status || "unknown");
  const groupId = String(event.group_id || "-");
  const summary = [
    liveAgentProcessEventPidLabel(event),
    liveAgentProcessEventRestartLabel(event),
    liveAgentProcessEventReasonLabel(event.reason) ? `reason ${liveAgentProcessEventReasonLabel(event.reason)}` : "",
    liveAgentProcessEventOfflineLabel(event.offline),
  ]
    .filter(Boolean)
    .join(" · ");
  return `
    <article class="live-agent-lifecycle-row live-agent-lifecycle-${escapeHtml(status)}">
      <div>
        <strong>${escapeHtml(eventType)}</strong>
        <span>${escapeHtml(groupId)} · ${escapeHtml(event.timestamp || "")}</span>
        ${summary ? `<small>${escapeHtml(summary)}</small>` : ""}
      </div>
      <em>${escapeHtml(status)}</em>
    </article>
  `;
}

function liveAgentProcessEventPidLabel(event) {
  const labels = [];
  const pid = Number(event.pid);
  if (event.pid !== null && event.pid !== undefined && String(event.pid).trim() !== "" && Number.isFinite(pid) && pid > 0) {
    labels.push(`pid ${pid}`);
  }
  const returncode = Number(event.returncode);
  if (
    event.returncode !== null &&
    event.returncode !== undefined &&
    String(event.returncode).trim() !== "" &&
    Number.isFinite(returncode)
  ) {
    labels.push(`returncode ${returncode}`);
  }
  return labels.join(" · ");
}

function liveAgentProcessEventRestartLabel(event) {
  const restartCount = Number(event.restart_count);
  const maxRestarts = Number(event.max_restarts);
  if (!Number.isFinite(restartCount) && !Number.isFinite(maxRestarts)) return "";
  const count = Number.isFinite(restartCount) ? Math.max(0, restartCount) : 0;
  const max = Number.isFinite(maxRestarts) ? Math.max(0, maxRestarts) : 0;
  const nextRestart = String(event.next_restart_at || "").trim();
  return `restart ${count}/${max}${nextRestart ? ` next ${nextRestart}` : ""}`;
}

function liveAgentProcessEventsTruncatedLabel(meta) {
  const scanned = Math.max(0, Number(meta.scannedEventCount || 0));
  return `searched recent ${scanned} lifecycle events; older matches may exist`;
}

function renderLiveAgentOperation(operation) {
  const status = String(operation.status || "unknown");
  const target = String(operation.target_id || "-");
  const summaryParts = [
    String(operation.summary || operation.error || "").trim(),
    liveAgentOperationDetailsLabel(operation.details, operation.operation),
  ].filter(Boolean);
  return `
    <article class="live-agent-operation-row live-agent-operation-${escapeHtml(status)}">
      <div>
        <strong>${escapeHtml(operation.operation || "unknown")}</strong>
        <span>${escapeHtml(target)} · ${escapeHtml(operation.timestamp || "")}</span>
        ${summaryParts.length ? `<small>${escapeHtml(summaryParts.join(" · "))}</small>` : ""}
      </div>
      <em>${escapeHtml(status)}</em>
    </article>
  `;
}

function liveAgentOperationDetailsLabel(details, operationName = "") {
  if (!details || typeof details !== "object" || Array.isArray(details)) return "";
  return orderedLiveAgentOperationDetails(details, operationName)
    .map(([key, value]) => liveAgentOperationDetailLabel(key, value))
    .filter(Boolean)
    .slice(0, liveAgentOperationDetailLimit(operationName))
    .join("; ");
}

function orderedLiveAgentOperationDetails(details, operationName = "") {
  const entries = [];
  const used = new Set();
  liveAgentOperationDetailPriority(operationName).forEach((key) => {
    if (Object.hasOwn(details, key)) {
      entries.push([key, details[key]]);
      used.add(key);
    }
  });
  Object.entries(details).forEach((entry) => {
    if (!used.has(entry[0])) entries.push(entry);
  });
  return entries;
}

function liveAgentOperationDetailPriority(operationName = "") {
  if (operationName === "session.smoke") {
    return [
      "result_status",
      "reply_count",
      "post_restart_reply_count",
      "post_recover_reply_count",
      "soak_cycle_count",
      "soak_reply_count",
      "soak_check_statuses",
    ];
  }
  if (operationName === "readiness.check") {
    return [
      "result_status",
      "health_process_reasons",
      "health_process_attention",
      "health_observation_attention",
      "health_observation_lobby_behind_count",
      "health_observation_live_behind_count",
      "health_observation_error_count",
      "health_shared_memory_attention",
      "health_session_run_attention",
      "health_session_run_retrying",
      "health_session_run_monitor_attention",
      "health_session_attention",
      "health_connection_attention",
      "health_agent_attention",
      "session_smoke_reply_count",
      "session_smoke_post_restart_reply_count",
      "session_smoke_post_recover_reply_count",
      "session_smoke_soak_cycle_count",
      "session_smoke_soak_reply_count",
      "session_smoke_soak_check_statuses",
      "probe_statuses",
    ];
  }
  if (["session.start", "session.ensure", "session.resume", "session.restart", "session.recover"].includes(operationName)) {
    return [
      "ensure_action",
      "result_status",
      "connected_agent_count",
      "reply_probe_status",
      "reply_probe_statuses",
      "auto_rounds_status",
      "auto_rounds_reason",
      "auto_rounds_answered_round_count",
      "auto_rounds_round_count",
    ];
  }
  if (operationName === "discovery.run") {
    return [
      "result_status",
      "approved_count",
      "approved_agent_ids",
      "approved_cli_count",
      "excluded_agent_count",
      "excluded_cli_count",
      "unmatched_approval_count",
      "agents",
      "discovered",
      "approval_required",
    ];
  }
  if (operationName === "review.checkpoint") {
    return [
      "result_status",
      "checkpoint_id",
      "answered_count",
      "timeout_count",
      "skipped_count",
      "agent_ids",
      "statuses",
      "reply_event_ids",
    ];
  }
  return [];
}

function liveAgentOperationDetailLimit(operationName = "") {
  if (operationName === "session.ensure") return 9;
  if (["session.start", "session.resume", "session.restart", "session.recover"].includes(operationName)) return 8;
  if (operationName === "readiness.check") return 12;
  if (operationName === "discovery.run") return 10;
  if (operationName === "review.checkpoint") return 8;
  return 7;
}

function liveAgentOperationDetailLabel(key, value) {
  const cleanKey = String(key || "").trim();
  const cleanValue = liveAgentOperationDetailValue(value);
  return cleanKey && cleanValue ? `${cleanKey}=${cleanValue}` : "";
}

function liveAgentOperationDetailValue(value) {
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  if (typeof value === "string") return value.trim();
  if (Array.isArray(value)) {
    return value
      .slice(0, 10)
      .map((item) => liveAgentOperationDetailValue(item))
      .filter(Boolean)
      .join(",");
  }
  return "";
}

function liveAgentProcessAgentsLabel(group) {
  const agents = Array.isArray(group.agents) ? group.agents : [];
  const labels = agents
    .map((agent) => {
      const name = String(agent.display_name || agent.agent_id || "").trim();
      const connection = connectionKindLabel(agent.connection_kind);
      return name ? `${name}/${connection}` : "";
    })
    .filter(Boolean);
  return labels.length ? `agents ${labels.join(", ")}` : "";
}

function liveAgentProcessConnectionLabel(group) {
  const connection = group.agent_connection;
  if (!connection || typeof connection !== "object") return "";
  const expected = Math.max(0, Number(connection.expected || 0));
  const connected = Math.max(0, Number(connection.connected || 0));
  const attention = liveAgentProcessConnectionAttentionLabel(connection.attention);
  if (!expected && !connected && !attention) return "";
  return `agents connected ${connected}/${expected}${attention ? ` · ${attention}` : ""}`;
}

function liveAgentProcessConnectionAttentionLabel(attention) {
  if (!Array.isArray(attention)) return "";
  return attention
    .map((item) => {
      if (!item || typeof item !== "object") return "";
      const agentId = String(item.agent_id || "").trim();
      const status = String(item.status || "").trim();
      return agentId && status ? `${status} ${agentId}` : "";
    })
    .filter(Boolean)
    .join(", ");
}

function liveAgentProcessEventLabel(group) {
  const events = Array.isArray(group.recent_events) ? group.recent_events : [];
  const latest = [...events].reverse().find((event) => event && event.event_type);
  if (!latest) return "";
  const timestamp = String(latest.timestamp || "").trim();
  const offline = liveAgentProcessLatestOfflineEventLabel(events, latest);
  const reason = liveAgentProcessLatestReasonEventLabel(events, latest);
  const details = [offline, reason, timestamp].filter(Boolean).join(" · ");
  return details ? `last event ${latest.event_type} · ${details}` : `last event ${latest.event_type}`;
}

function liveAgentProcessLatestReasonEventLabel(events, latest) {
  const reasonEvent = [...events].reverse().find((event) => {
    if (!event || !event.event_type) return false;
    return Boolean(liveAgentProcessEventReasonLabel(event.reason));
  });
  if (!reasonEvent) return "";
  const reason = liveAgentProcessEventReasonLabel(reasonEvent.reason);
  if (!reason) return "";
  return reasonEvent === latest ? `reason ${reason}` : `last reason ${reasonEvent.event_type} ${reason}`;
}

function liveAgentProcessEventReasonLabel(value) {
  return String(value || "").trim();
}

function liveAgentProcessLatestOfflineEventLabel(events, latest) {
  const offlineEvent = [...events].reverse().find((event) => {
    if (!event || !event.event_type) return false;
    return Boolean(liveAgentProcessEventOfflineLabel(event.offline));
  });
  if (!offlineEvent) return "";
  const offline = liveAgentProcessEventOfflineLabel(offlineEvent.offline);
  if (!offline) return "";
  return offlineEvent === latest ? offline : `last offline ${offlineEvent.event_type} ${offline}`;
}

function liveAgentProcessEventOfflineLabel(value) {
  if (!value || typeof value !== "object") return "";
  const expected = Math.max(0, Number(value.expected || 0));
  const offline = Math.max(0, Number(value.offline || 0));
  if (!expected) return "";
  const attention = liveAgentProcessEventOfflineAttentionLabel(value.attention);
  return attention ? `offline ${offline}/${expected} · ${attention}` : `offline ${offline}/${expected}`;
}

function liveAgentProcessEventOfflineAttentionLabel(value) {
  if (!Array.isArray(value)) return "";
  return value
    .slice(0, 10)
    .map((item) => {
      if (!item || typeof item !== "object") return "";
      const agentId = String(item.agent_id || "").trim();
      const status = String(item.status || "").trim();
      return agentId && status ? `${status} ${agentId}` : "";
    })
    .filter(Boolean)
    .join(", ");
}

function liveAgentProcessMeetingLabel(group) {
  const meetingId = String(group.meeting_id || "").trim();
  return meetingId ? `meeting ${meetingId}` : "";
}

function renderLiveAgentCard(agent) {
  const status = agent.status || "offline";
  const runtimeDetails = liveAgentRuntimeDetails(agent);
  const contractDetails = liveAgentContractDetails(agent);
  const characterDetails = liveAgentCharacterDetails(agent);
  const admissionDetails = liveAgentAdmissionDetails(agent);
  const lastError = String(agent.last_error || "").trim();
  const agentId = String(agent.agent_id || "");
  const probeRunning = state.liveAgentProbeRunning === agentId;
  return `
    <article class="live-agent-card live-agent-${escapeHtml(status)}">
      <div>
        <strong>${escapeHtml(agent.display_name || agent.agent_id || "agent")}</strong>
        <span>${escapeHtml(agent.agent_id || "")}</span>
      </div>
      <em>${escapeHtml(liveAgentStatusLabel(status))}</em>
      <small>${escapeHtml(providerKindLabel(agent.provider_kind))} · ${escapeHtml(connectionKindLabel(agent.connection_kind))} · ${escapeHtml(agent.engagement_mode || "mentioned")}</small>
      <select class="live-agent-engagement" data-live-agent-engagement="${escapeHtml(agent.agent_id || "")}" aria-label="engagement mode">
        ${renderEngagementModeOptions(agent.engagement_mode)}
      </select>
      <button type="button" class="live-agent-probe" data-live-agent-probe="${escapeHtml(agentId)}" ${probeRunning || !agentId ? "disabled" : ""}>probe</button>
      ${contractDetails ? `<small class="live-agent-contract">${escapeHtml(contractDetails)}</small>` : ""}
      ${characterDetails ? `<small class="live-agent-character">${escapeHtml(characterDetails)}</small>` : ""}
      ${admissionDetails ? `<small class="live-agent-admission">${escapeHtml(admissionDetails)}</small>` : ""}
      ${runtimeDetails ? `<small class="live-agent-runtime">${escapeHtml(runtimeDetails)}</small>` : ""}
      ${lastError ? `<small class="live-agent-error-detail">${escapeHtml(lastError)}</small>` : ""}
    </article>
  `;
}

function liveAgentCharacterDetails(agent) {
  const mode = String(agent.character_mode || "off");
  const cardId = String(agent.persona_card_id || "").trim();
  if (mode === "off" && !cardId) return "";
  const label = mode === "work_speech_only" ? "Work speech" : mode === "on" ? "ON" : "OFF";
  return `Character ${label}${cardId ? ` · ${cardId}` : ""}`;
}

function renderEngagementModeOptions(currentMode) {
  const current = String(currentMode || "mentioned");
  return [
    ["always", "always (loop-prone)"],
    ["flow", "flow"],
    ["human_only", "human only"],
    ["mentioned", "mentioned"],
    ["moderator_called", "moderator called"],
    ["watch", "watch"],
    ["manual", "manual"],
  ]
    .map(([value, label]) => `<option value="${escapeHtml(value)}" ${value === current ? "selected" : ""}>${escapeHtml(label)}</option>`)
    .join("");
}

function liveAgentRuntimeDetails(agent) {
  const details = [];
  const heartbeatAge = heartbeatAgeLabel(agent);
  if (heartbeatAge) details.push(heartbeatAge);
  if (agent.last_reply_at) details.push(`reply ${agent.last_reply_at}`);
  if (agent.last_attention) details.push(`attention ${agent.last_attention}`);
  if (agent.last_observed_event_id) details.push(`last read lobby ${shortSessionId(agent.last_observed_event_id)}`);
  if (agent.last_observed_live_event_id) details.push(`last read official ${shortSessionId(agent.last_observed_live_event_id)}`);
  return details.join(" · ");
}

function liveAgentContractDetails(agent) {
  const details = [];
  if (agent.join_semantics) details.push(agent.join_semantics);
  if (agent.context_durability) details.push(agent.context_durability);
  if (agent.sandbox_enforcement) details.push(`sandbox ${agent.sandbox_enforcement}`);
  return details.join(" · ");
}

function liveAgentAdmissionDetails(agent) {
  const details = [];
  if (agent.admission_status) details.push(agent.admission_status);
  if (agent.host_approved_binding === true) details.push("host-approved");
  if (agent.host_approved_binding === false) details.push("not-approved");
  if (Array.isArray(agent.binding_conflicts) && agent.binding_conflicts.length) {
    details.push(`conflict ${agent.binding_conflicts.slice(0, 3).join(", ")}`);
  }
  return details.join(" · ");
}

function heartbeatAgeLabel(agent) {
  const age = Number(agent.heartbeat_age_seconds);
  if (!Number.isFinite(age) || age < 0) return "";
  const roundedAge = Math.round(age);
  const staleAfter = Number(agent.stale_after_seconds);
  if (!Number.isFinite(staleAfter) || staleAfter < 0) return `seen ${roundedAge}s ago`;
  return `seen ${roundedAge}s ago / stale ${Math.round(staleAfter)}s`;
}

function renderLiveAgentProviderOptions() {
  return [
    ["claude_code", "Claude Code"],
    ["gemini", "Gemini"],
    ["grok", "Grok"],
    ["local_openai_compatible", "OpenAI-compatible"],
    ["remote_http_bridge", "Remote HTTP"],
    ["codex_live_session", "Codex Live"],
    ["kiro_live_session", "Kiro Live"],
    ["manual", "Manual"],
  ]
    .map(([value, label]) => `<option value="${escapeHtml(value)}">${escapeHtml(label)}</option>`)
    .join("");
}

function renderApprovedBindings(meeting) {
  const roles = meeting?.roles || [];
  const status = state.providerHealthStatus;
  const source = meeting?.agent_config_source || "";
  if (!roles.length) return "";
  return `
    <section class="approved-bindings" aria-label="승인된 본회의 에이전트">
      <div class="roster-head">
        <strong>본회의 승인</strong>
        <span>host가 확정한 role → agent → provider</span>
      </div>
      <div class="room-actions">
        <button type="button" id="provider-health-check" ${!source || source === "default" || state.providerHealthRunning ? "disabled" : ""}>Provider 점검</button>
      </div>
      ${roles.map(renderApprovedBinding).join("")}
      ${status ? `<p class="live-agent-status" data-tone="${escapeHtml(status.tone || "info")}">${escapeHtml(status.message)}</p>` : ""}
    </section>
  `;
}

function renderApprovedBinding(role) {
  const { binding, provider, permissions } = bindingSummary(state.payload?.meeting, role.id);
  const meta = roleMeta[role.id] || { color: "purple", badge: role.lens };
  const permissionLabel = permissions?.implementation ? "구현" : permissions?.filesystem_write ? "쓰기" : "회의";
  const providerLabel = providerDisplayName(provider, binding);
  const joinLabel = joinModeLabel(binding?.join_mode);
  const sessionBadge = binding?.session_id
    ? ` · <small class="binding-session" title="${escapeHtml(binding.session_id)}">${escapeHtml(shortSessionId(binding.session_id))}</small>`
    : "";
  return `
    <div class="approved-binding binding-${escapeHtml(meta.color)}">
      <strong>${escapeHtml(role.display_name)}</strong>
      <span>${escapeHtml(binding?.agent_id || "unbound")}</span>
      <em>${escapeHtml(providerLabel)} · ${escapeHtml(permissionLabel)} · ${escapeHtml(joinLabel)}${sessionBadge}</em>
    </div>
  `;
}

function renderCodexSessionInvite(meeting) {
  const roles = meeting?.roles || [];
  const sessions = state.codexSessions || [];
  const disabled = !roles.length || !sessions.length || state.codexSessionsLoading;
  const status = state.codexInviteStatus;
  return `
    <section class="codex-session-invite" aria-label="Codex 세션 초대">
      <div class="roster-head">
        <strong>Codex 세션 초대</strong>
        <span>최근 세션 → 본회의 역할</span>
      </div>
      <form id="codex-invite-form" class="codex-invite-form">
        <select id="codex-session-select" ${disabled ? "disabled" : ""}>
          ${renderCodexSessionOptions(sessions)}
        </select>
        <select id="codex-role-select" ${!roles.length ? "disabled" : ""}>
          ${roles.map((role) => `<option value="${escapeHtml(role.id)}">${escapeHtml(role.display_name || role.id)}</option>`).join("")}
        </select>
        <button type="submit" ${disabled ? "disabled" : ""}>초대</button>
        <button type="button" id="codex-session-join" ${disabled ? "disabled" : ""}>입장</button>
        <button type="button" id="codex-session-refresh">갱신</button>
      </form>
      ${status ? `<p class="codex-invite-status" data-tone="${escapeHtml(status.tone || "info")}">${escapeHtml(status.message)}</p>` : ""}
    </section>
  `;
}

function renderCodexSessionOptions(sessions) {
  if (state.codexSessionsLoading) return '<option value="">불러오는 중</option>';
  if (!sessions.length) return '<option value="">최근 세션 없음</option>';
  return sessions
    .map(
      (session) =>
        `<option value="${escapeHtml(session.id)}">${escapeHtml(codexSessionOptionLabel(session))}</option>`
    )
    .join("");
}

function codexSessionOptionLabel(session) {
  const title = session.thread_name || "Untitled";
  const updated = session.updated_at ? ` · ${session.updated_at}` : "";
  return `${title} · ${shortSessionId(session.id)}${updated}`;
}

function providerDisplayName(provider, binding) {
  if (provider?.kind === "codex_live_session") return "Codex Live";
  if (provider?.kind === "kiro_live_session") return "Kiro Live";
  return provider?.display_name || binding?.provider_id || "provider 없음";
}

function joinModeLabel(joinMode) {
  if (joinMode === "current_session") return "이어받은 세션";
  if (joinMode === "imported_pack") return "가져온 기억";
  return "새 세션";
}

function shortSessionId(sessionId) {
  const value = String(sessionId || "");
  if (value.length <= 14) return value;
  return `${value.slice(0, 8)}...${value.slice(-4)}`;
}

function liveAgentStatusLabel(status) {
  if (status === "online") return "온라인";
  if (status === "working") return "작업 중";
  if (status === "error") return "오류";
  if (status === "stale") return "응답 지연";
  return "오프라인";
}

function liveAgentProcessStatusLabel(status) {
  if (status === "running") return "실행 중";
  if (status === "restarting") return "재시작 대기";
  if (status === "error") return "오류";
  if (status === "stopped") return "중지됨";
  return "상태 미정";
}

function liveAgentProcessRestartLabel(group) {
  if (!group?.auto_restart) return "auto restart off";
  const count = group.restart_count ?? 0;
  const max = group.max_restarts ?? 0;
  const backoff = group.restart_backoff_seconds ?? 0;
  const parts = [`auto restart ${count}/${max}`, `backoff ${backoff}s`];
  const staleWatchdog = liveAgentProcessStaleWatchdogLabel(group.stale_restart_after_seconds);
  const nextRestart = String(group.next_restart_at || "").trim();
  if (staleWatchdog) parts.push(staleWatchdog);
  if (nextRestart) parts.push(`next restart ${nextRestart}`);
  return parts.join(" · ");
}

function liveAgentProcessStaleWatchdogLabel(value) {
  const seconds = Number(value);
  if (!Number.isFinite(seconds) || seconds <= 0) return "";
  return Number.isInteger(seconds) ? `stale watchdog ${seconds}s` : `stale watchdog ${seconds.toFixed(1)}s`;
}

function providerKindLabel(kind) {
  if (kind === "claude_code") return "Claude Code";
  if (kind === "gemini") return "Gemini";
  if (kind === "grok") return "Grok";
  if (kind === "local_openai_compatible") return "OpenAI-compatible";
  if (kind === "remote_http_bridge") return "Remote HTTP";
  if (kind === "codex_live_session") return "Codex Live";
  if (kind === "kiro_live_session") return "Kiro Live";
  return kind || "Manual";
}

function connectionKindLabel(kind) {
  if (kind === "local_cli") return "Local CLI";
  if (kind === "live_session") return "Live session";
  if (kind === "terminal_session") return "Terminal session";
  if (kind === "self_service") return "Self-service";
  if (kind === "remote_bridge") return "Remote bridge";
  if (kind === "codex_resume") return "Codex resume";
  return "Manual";
}

function renderRosterUser(user) {
  return `
    <section class="roster-user roster-${escapeHtml(user.key)}">
      <div class="roster-user-title">
        <span class="roster-avatar">${escapeHtml(initials(user.name))}</span>
        <div>
          <strong>${escapeHtml(user.name)}</strong>
          <small>${escapeHtml(user.messageCount)}개 이벤트</small>
        </div>
        <button type="button" data-lobby-action="ready" data-lobby-side="${escapeHtml(user.key)}" data-lobby-name="${escapeHtml(user.name)}">준비</button>
      </div>
      <div class="roster-agents">
        ${
          user.agents.length
            ? user.agents.map((agent) => renderRosterAgent(agent, user.key)).join("")
            : '<span class="roster-none">대기 중인 에이전트 없음</span>'
        }
      </div>
    </section>
  `;
}

function renderRosterAgent(agent, ownerKey) {
  const state = agent.deploy ? "deploy" : agent.ready ? "준비" : "대기";
  const side = ownerKey === "mine" ? "my-agent" : "other-agent";
  return `
    <div class="roster-agent">
      <span>${escapeHtml(agent.name)}</span>
      <em>${escapeHtml(state)}</em>
      <button type="button" data-lobby-action="deploy" data-lobby-side="${escapeHtml(side)}" data-lobby-name="${escapeHtml(agent.name)}">투입</button>
    </div>
  `;
}

function initials(name) {
  return String(name || "?").trim().slice(0, 2).toUpperCase();
}

function lobbySideLabel(side) {
  if (side === "mine") return "나";
  if (side === "my-agent") return "내 에이전트";
  if (side === "other-agent") return "상대 에이전트";
  return "상대";
}

function lobbyKindLabel(kind) {
  if (kind === "ready") return "준비";
  if (kind === "deploy") return "투입";
  return "메시지";
}

function defaultLobbyMessage(kind, side) {
  if (kind === "ready") return `${lobbySideLabel(side)} 준비 완료`;
  if (kind === "deploy") return `${lobbySideLabel(side)} 투입 요청`;
  return "";
}

async function uploadLobbyAttachments(files) {
  const selected = Array.from(files || []);
  if (!selected.length) return;
  lobbyAttachmentStatus = "첨부 업로드 중";
  renderLobby({ followLatest: false });
  try {
    const uploaded = [];
    for (const file of selected) {
      const dataBase64 = await fileToBase64(file);
      const payload = await fetchJson("/api/attachments", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          filename: file.name || "attachment.bin",
          content_type: file.type || "application/octet-stream",
          data_base64: dataBase64,
        }),
      });
      if (payload.attachment) uploaded.push(payload.attachment);
    }
    pendingLobbyAttachments = [...pendingLobbyAttachments, ...uploaded];
    lobbyAttachmentStatus = uploaded.length ? `${uploaded.length}개 첨부됨` : "";
  } catch (error) {
    lobbyAttachmentStatus = `첨부 실패: ${error?.message || "알 수 없는 오류"}`;
  }
  renderLobby({ followLatest: false });
}

function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.addEventListener("load", () => {
      const result = String(reader.result || "");
      resolve(result.includes(",") ? result.split(",", 2)[1] : result);
    });
    reader.addEventListener("error", () => reject(reader.error || new Error("file read failed")));
    reader.readAsDataURL(file);
  });
}

function removePendingLobbyAttachment(attachmentId) {
  pendingLobbyAttachments = pendingLobbyAttachments.filter((attachment) => attachment.id !== attachmentId);
  lobbyAttachmentStatus = pendingLobbyAttachments.length ? lobbyAttachmentStatus : "";
  renderLobby({ followLatest: false });
}

async function sendLobbyEvent(kind, options = {}) {
  const lobby = document.querySelector("#lobby");
  const messageInput = document.querySelector("#lobby-message");
  const side = "mine";
  const name = localStorage.getItem("agentsassemble.name") || defaultLobbyName(side);
  const previousValue = messageInput?.value || "";
  const message = previousValue.trim();
  const attachments = pendingLobbyAttachments;
  if (kind === "message" && !message && !attachments.length) return;
  const shouldFollowLatest = isLobbyFeedNearBottom(lobby);
  if (messageInput && kind === "message") messageInput.value = "";
  let payload;
  try {
    payload = await fetchJson("/api/lobby", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, side, kind, message, attachments }),
    });
  } catch (error) {
    const activeInput = document.querySelector("#lobby-message");
    if (activeInput && kind === "message" && activeInput.value === "") {
      activeInput.value = previousValue;
      activeInput.focus();
    }
    throw error;
  }
  pendingLobbyAttachments = [];
  lobbyAttachmentStatus = "";
  setLobbyEvents(payload.events || []);
  refreshLobbyFeed({ followLatest: shouldFollowLatest });
  document.querySelector("#lobby-message")?.focus();
  if (options.askRemote && message) {
    await sendLobbyRemote(message, name, { followLatest: shouldFollowLatest });
  }
}

async function sendLobbyRemote(message, speakerName, options = {}) {
  const payload = await fetchJson("/api/lobby/remote", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      meeting_id: state.payload?.meeting?.meeting_id,
      speaker_name: speakerName,
      message,
    }),
  });
  setLobbyEvents(payload.events || []);
  refreshLobbyFeed({ followLatest: options.followLatest ?? isLobbyFeedNearBottom(document.querySelector("#lobby")) });
  document.querySelector("#lobby-message")?.focus();
}

function liveAgentListRenderSignature(agents) {
  return JSON.stringify((agents || []).map((agent) => {
    const copy = { ...agent };
    delete copy.heartbeat_age_seconds;
    return copy;
  }));
}

function liveAgentHealthRenderSignature(payload) {
  const clone = cloneJson(payload || null);
  if (clone?.process_monitor) delete clone.process_monitor.last_tick_at;
  if (clone?.session_run_monitor) delete clone.session_run_monitor.last_tick_at;
  return JSON.stringify(clone);
}

function cloneJson(value) {
  if (value === null || value === undefined) return value;
  return JSON.parse(JSON.stringify(value));
}

async function loadLiveAgents(options = {}) {
  if (state.liveAgentsLoading && !options.force) return;
  const previousSignature = liveAgentListRenderSignature(state.liveAgents || []);
  let shouldRender = !options.background;
  state.liveAgentsLoading = true;
  if (options.force) state.liveAgentStatus = { message: "살아있는 에이전트 갱신 중", tone: "info" };
  if (!options.background) renderLobby({ followLatest: false });
  try {
    const payload = await fetchJson("/api/live-agents");
    const agents = payload.agents || [];
    setLiveAgents(agents);
    state.liveAgentsLoaded = true;
    shouldRender = shouldRender || liveAgentListRenderSignature(agents) !== previousSignature;
    if (
      state.liveAgentStatus?.message === "살아있는 에이전트 갱신 중" ||
      state.liveAgentStatus?.message === "살아있는 에이전트 목록을 불러오지 못했습니다."
    ) {
      state.liveAgentStatus = null;
      shouldRender = true;
    }
  } catch {
    state.liveAgentStatus = { message: "살아있는 에이전트 목록을 불러오지 못했습니다.", tone: "error" };
    state.liveAgentsLoaded = true;
    shouldRender = true;
  } finally {
    state.liveAgentsLoading = false;
    if (shouldRender) renderLobby({ followLatest: false });
  }
}

async function loadLiveAgentProcesses(options = {}) {
  if (state.liveAgentProcessesLoading && !options.force) return;
  const previousSignature = JSON.stringify(state.liveAgentProcesses || []);
  let shouldRender = !options.background;
  state.liveAgentProcessesLoading = true;
  if (options.force) state.liveAgentProcessStatus = { message: "상주 실행 상태 갱신 중", tone: "info" };
  if (!options.background) renderLobby({ followLatest: false });
  try {
    const payload = await fetchJson("/api/live-agent-processes");
    const groups = payload.groups || [];
    setLiveAgentProcesses(groups);
    state.liveAgentProcessesLoaded = true;
    shouldRender = shouldRender || JSON.stringify(groups) !== previousSignature;
    if (
      state.liveAgentProcessStatus?.message === "상주 실행 상태 갱신 중" ||
      state.liveAgentProcessStatus?.message === "상주 실행 상태를 불러오지 못했습니다."
    ) {
      state.liveAgentProcessStatus = null;
      shouldRender = true;
    }
  } catch {
    state.liveAgentProcessStatus = { message: "상주 실행 상태를 불러오지 못했습니다.", tone: "error" };
    state.liveAgentProcessesLoaded = true;
    shouldRender = true;
  } finally {
    state.liveAgentProcessesLoading = false;
    if (shouldRender) renderLobby({ followLatest: false });
  }
}

export function refreshLiveAgentRuntimeSurfaces() {
  return Promise.all([
    loadLiveAgentHealth({ background: true }),
    loadLiveAgents({ background: true }),
    loadLiveAgentFlow({ background: true }),
    loadLiveAgentProcesses({ background: true }),
    loadLiveAgentProcessEvents({ background: true }),
    loadLiveAgentOperations({ background: true }),
    loadLiveAgentSessionRuns({ background: true }),
  ]);
}

async function loadLiveAgentFlow(options = {}) {
  if (state.liveAgentFlowLoading && !options.force) return;
  const previousSignature = JSON.stringify(state.liveAgentFlow || null);
  let shouldRender = !options.background;
  state.liveAgentFlowLoading = true;
  try {
    const meetingId = liveAgentMeetingId();
    const query = meetingId ? `?meeting_id=${encodeURIComponent(meetingId)}` : "";
    const payload = await fetchJson(`/api/live-agent-flow${query}`);
    state.liveAgentFlow = payload.flow || { status: "idle" };
    state.liveAgentFlowEvents = payload.flow_events || [];
    state.liveAgentFlowLoaded = true;
    shouldRender = shouldRender || JSON.stringify(state.liveAgentFlow) !== previousSignature;
  } catch {
    state.liveAgentFlowLoaded = true;
  } finally {
    state.liveAgentFlowLoading = false;
    notifyLiveAgentFlowUpdated();
    if (shouldRender && !(options.background && patchLiveAgentFlowPanel())) {
      renderLobby({ followLatest: false });
    }
  }
}

function liveAgentMeetingId() {
  return state.payload?.meeting?.meeting_id || document.querySelector("#live-agent-session-meeting-id")?.value.trim() || "";
}

function patchLiveAgentFlowPanel() {
  const panel = document.querySelector(".live-agent-flow-panel");
  const status = panel?.querySelector(".live-agent-flow-status");
  if (!panel || !status) return false;
  const diagnostics = panel.querySelector(".live-agent-flow-diagnostics");
  if (Boolean(diagnostics) !== Boolean(renderLiveAgentFlowDiagnostics(state.liveAgentFlow))) return false;
  status.textContent = liveAgentFlowStatusLabel(state.liveAgentFlow);
  const diagnosticsText = panel.querySelector(".live-agent-flow-diagnostics span");
  if (diagnosticsText) diagnosticsText.textContent = liveAgentFlowDiagnosticParts(state.liveAgentFlow).join(" · ");
  return true;
}

async function loadLiveAgentHealth(options = {}) {
  if (state.liveAgentHealthLoading && !options.force) return;
  const previousSignature = liveAgentHealthRenderSignature(state.liveAgentHealth || null);
  let shouldRender = !options.background;
  state.liveAgentHealthLoading = true;
  if (!options.background) renderLobby({ followLatest: false });
  try {
    const payload = await fetchJson("/api/live-agent-health");
    state.liveAgentHealth = payload;
    state.liveAgentHealthLoaded = true;
    shouldRender = shouldRender || liveAgentHealthRenderSignature(payload) !== previousSignature;
  } catch {
    state.liveAgentHealth = { status: "unknown" };
    state.liveAgentHealthLoaded = true;
    shouldRender = true;
  } finally {
    state.liveAgentHealthLoading = false;
    if (shouldRender) renderLobby({ followLatest: false });
  }
}

async function loadLiveAgentOperations(options = {}) {
  if (state.liveAgentOperationsLoading && !options.force) return;
  const previousSignature = JSON.stringify(state.liveAgentOperations || []);
  let shouldRender = !options.background;
  state.liveAgentOperationsLoading = true;
  try {
    const payload = await fetchJson("/api/live-agent-operations?limit=20");
    const operations = payload.operations || [];
    setLiveAgentOperations(operations);
    state.liveAgentOperationsLoaded = true;
    shouldRender = shouldRender || JSON.stringify(operations) !== previousSignature;
  } catch {
    state.liveAgentOperationsLoaded = true;
  } finally {
    state.liveAgentOperationsLoading = false;
    if (shouldRender) renderLobby({ followLatest: false });
  }
}

async function loadLiveAgentSessionRuns(options = {}) {
  if (state.liveAgentSessionRunsLoading && !options.force) return;
  const previousSignature = JSON.stringify(state.liveAgentSessionRuns || []);
  let shouldRender = !options.background;
  state.liveAgentSessionRunsLoading = true;
  try {
    const payload = await fetchJson("/api/live-agent-session-runs?limit=20&include_readiness=1");
    const runs = payload.runs || [];
    setLiveAgentSessionRuns(runs);
    state.liveAgentSessionRunsLoaded = true;
    shouldRender = shouldRender || JSON.stringify(runs) !== previousSignature;
  } catch {
    state.liveAgentSessionRunsLoaded = true;
  } finally {
    state.liveAgentSessionRunsLoading = false;
    if (shouldRender) renderLobby({ followLatest: false });
  }
}

function refreshLiveAgentProcessHistory() {
  return Promise.all([
    loadLiveAgentProcessEvents({ background: true, force: true }),
    loadLiveAgentOperations({ background: true, force: true }),
    loadLiveAgentSessionRuns({ background: true, force: true }),
  ]);
}

async function loadLiveAgentProcessEvents(options = {}) {
  if (state.liveAgentProcessEventsLoading && !options.force) return;
  const previousSignature = JSON.stringify({
    events: state.liveAgentProcessEvents || [],
    meta: state.liveAgentProcessEventsMeta || null,
  });
  let shouldRender = !options.background;
  state.liveAgentProcessEventsLoading = true;
  try {
    const payload = await fetchJson("/api/live-agent-process-events?limit=20");
    const events = Array.isArray(payload.events) ? payload.events : [];
    state.liveAgentProcessEvents = events;
    state.liveAgentProcessEventsMeta = liveAgentProcessEventsMeta(payload);
    state.liveAgentProcessEventsLoaded = true;
    shouldRender =
      shouldRender ||
      JSON.stringify({
        events: state.liveAgentProcessEvents,
        meta: state.liveAgentProcessEventsMeta,
      }) !== previousSignature;
  } catch {
    state.liveAgentProcessEventsLoaded = true;
  } finally {
    state.liveAgentProcessEventsLoading = false;
    if (shouldRender) renderLobby({ followLatest: false });
  }
}

function liveAgentProcessEventsMeta(payload) {
  return {
    limit: Math.max(0, Number(payload.limit || 0)),
    groupId: String(payload.group_id || "").trim(),
    scanLimit: Math.max(0, Number(payload.scan_limit || 0)),
    scannedEventCount: Math.max(0, Number(payload.scanned_event_count || 0)),
    truncated: Boolean(payload.truncated),
  };
}

async function runProviderHealthCheck() {
  if (state.providerHealthRunning) return;
  const configPath = state.payload?.meeting?.agent_config_source || "";
  if (!configPath || configPath === "default") {
    state.providerHealthStatus = { message: "provider config 없음", tone: "error" };
    renderLobby({ followLatest: false });
    return;
  }
  state.providerHealthRunning = true;
  state.providerHealthStatus = { message: "provider health 점검 중", tone: "info" };
  renderLobby({ followLatest: false });
  try {
    const payload = await fetchJson("/api/provider-health", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ config_path: configPath }),
    });
    const tone = payload.status === "ok" ? "success" : "error";
    const summary = payload.summary || {};
    state.providerHealthStatus = { message: `provider health ${payload.status || "unknown"} · ${summary.providers || 0} providers`, tone };
  } catch {
    state.providerHealthStatus = { message: "provider health 점검 실패", tone: "error" };
  } finally {
    state.providerHealthRunning = false;
    renderLobby({ followLatest: false });
  }
}

async function runLiveAgentSmoke(lobby) {
  if (liveAgentProcessActionBusy()) return;
  const groupId = lobby.querySelector("#live-agent-process-group")?.value.trim() || "";
  state.liveAgentSmokeRunning = true;
  state.liveAgentProcessStatus = { message: "상주 smoke 진단 중", tone: "info" };
  renderLobby({ followLatest: false });
  try {
    const payload = await fetchJson("/api/live-agent-smoke", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ group_id: groupId, timeout: 12 }),
    });
    try {
      const lobbyPayload = await fetchJson("/api/lobby");
      setLobbyEvents(lobbyPayload.events || []);
    } catch {
      // The smoke itself succeeded; transient lobby refresh failure should not hide that result.
    }
    await refreshLiveAgentRuntimeSurfaces();
    state.liveAgentProcessStatus = { message: `smoke 진단 통과: ${payload.group_id || "live-agent-smoke"}`, tone: "success" };
  } catch {
    state.liveAgentProcessStatus = { message: "smoke 진단 실패", tone: "error" };
  } finally {
    state.liveAgentSmokeRunning = false;
    renderLobby({ followLatest: false });
  }
}

async function runLiveAgentOfficialRoundSmoke(lobby) {
  if (liveAgentProcessActionBusy()) return;
  const groupId = lobby.querySelector("#live-agent-process-group")?.value.trim() || "";
  state.liveAgentOfficialRoundSmokeRunning = true;
  state.liveAgentProcessStatus = { message: "공식 라운드 smoke 진단 중", tone: "info" };
  renderLobby({ followLatest: false });
  try {
    const payload = await fetchJson("/api/live-agent-official-round-smoke", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ group_id: groupId, timeout: 12 }),
    });
    await refreshLiveAgentRuntimeSurfaces();
    const tone = payload.status === "ok" ? "success" : "error";
    state.liveAgentProcessStatus = { message: `공식 라운드 smoke ${payload.status || "unknown"} · ${officialRoundSmokeCountsLabel(payload)}`, tone };
  } catch {
    state.liveAgentProcessStatus = { message: "공식 라운드 smoke 진단 실패", tone: "error" };
  } finally {
    state.liveAgentOfficialRoundSmokeRunning = false;
    renderLobby({ followLatest: false });
  }
}

async function runLiveAgentSessionSmoke(lobby) {
  if (liveAgentProcessActionBusy()) return;
  state.liveAgentSessionSmokeRunning = true;
  state.liveAgentProcessStatus = { message: "상주 세션 smoke 진단 중", tone: "info" };
  renderLobby({ followLatest: false });
  try {
    const payload = await fetchJson("/api/live-agent-session-smoke", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(liveAgentSessionSmokeRequestBody(lobby)),
    });
    await refreshLiveAgentSessionSmokeSurfaces();
    const tone = payload.status === "ok" ? "success" : "error";
    state.liveAgentProcessStatus = { message: liveAgentSessionSmokeStatusMessage(payload), tone };
  } catch {
    await refreshLiveAgentSessionSmokeSurfaces();
    state.liveAgentProcessStatus = { message: "상주 세션 smoke 진단 실패", tone: "error" };
  } finally {
    state.liveAgentSessionSmokeRunning = false;
    renderLobby({ followLatest: false });
  }
}

async function refreshLiveAgentSessionSmokeSurfaces() {
  try {
    const lobbyPayload = await fetchJson("/api/lobby");
    setLobbyEvents(lobbyPayload.events || []);
  } catch {
    // The session smoke result remains useful if only the lobby refresh fails.
  }
  await refreshLiveAgentRuntimeSurfaces();
}

async function runLiveAgentReadiness(lobby) {
  if (liveAgentProcessActionBusy()) return;
  const groupId = lobby.querySelector("#live-agent-process-group")?.value.trim() || "";
  const includeOfficialRound = lobby.querySelector("#live-agent-readiness-official-round")?.checked === true;
  const includeSessionSmoke = lobby.querySelector("#live-agent-readiness-session-smoke")?.checked === true;
  const requestBody = { group_id: groupId, timeout: 12 };
  if (includeOfficialRound) requestBody.official_round_smoke = true;
  if (includeSessionSmoke) {
    requestBody.session_smoke = true;
    const soakCycles = liveAgentSessionSmokeSoakCycles(lobby);
    if (soakCycles > 0) {
      requestBody.session_smoke_soak_cycle_count = soakCycles;
      requestBody.session_smoke_soak_interval_seconds = liveAgentSessionSmokeSoakIntervalSeconds(lobby);
    }
  }
  state.liveAgentReadinessRunning = true;
  state.liveAgentProcessStatus = { message: "상주 readiness 점검 중", tone: "info" };
  renderLobby({ followLatest: false });
  try {
    const payload = await fetchJson("/api/live-agent-readiness", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(requestBody),
    });
    try {
      const lobbyPayload = await fetchJson("/api/lobby");
      setLobbyEvents(lobbyPayload.events || []);
    } catch {
      // Readiness result is still useful if only the post-check lobby refresh fails.
    }
    await refreshLiveAgentRuntimeSurfaces();
    const tone = payload.status === "ready" ? "success" : "error";
    state.liveAgentProcessStatus = { message: liveAgentReadinessStatusMessage(payload), tone };
  } catch {
    state.liveAgentProcessStatus = { message: "readiness 점검 실패", tone: "error" };
  } finally {
    state.liveAgentReadinessRunning = false;
    renderLobby({ followLatest: false });
  }
}

function liveAgentReadinessStatusMessage(payload) {
  const status = payload.status || "unknown";
  const officialRoundSmoke = payload.official_round_smoke;
  const sessionSmoke = payload.session_smoke;
  const parts = [];
  if (officialRoundSmoke && typeof officialRoundSmoke === "object") {
    parts.push(`official ${officialRoundSmokeCountsLabel(officialRoundSmoke)}`);
  }
  if (sessionSmoke && typeof sessionSmoke === "object") {
    parts.push(`session ${sessionSmokeStatusLabel(sessionSmoke)}`);
  }
  return [`readiness ${status}`, ...parts].join(" · ");
}

function officialRoundSmokeCountsLabel(payload) {
  const answered = payload.answered_count || 0;
  const timedOut = payload.timeout_count || 0;
  const skipped = payload.skipped_count || 0;
  return `${answered} answered, ${timedOut} timed out, ${skipped} skipped`;
}

function sessionSmokeStatusLabel(payload) {
  const status = payload.status || "unknown";
  if (status !== "ok") {
    const reason = payload.reason || payload.error || "";
    return reason ? `${status}: ${reason}` : status;
  }
  const expectedReplies = Math.max(0, Number(payload.expected_reply_count || 0));
  const lobbyProbeCount = Math.max(1, Number(payload.lobby_probe_count || 1));
  const expectedReplyTotal = expectedReplies * lobbyProbeCount;
  const replies = Math.max(0, Number(payload.reply_count || 0));
  const postRestartReplies = Math.max(0, Number(payload.post_restart_reply_count || 0));
  const postRecoverReplies = Math.max(0, Number(payload.post_recover_reply_count || 0));
  const soakCycles = Math.max(0, Number(payload.soak_cycle_count || 0));
  const soakReplies = Math.max(0, Number(payload.soak_reply_count || 0));
  const expectedSoakReplies = expectedReplies * soakCycles;
  const soakLabel = soakCycles > 0 ? `, soak ${soakReplies}/${expectedSoakReplies} over ${soakCycles} cycles` : "";
  return (
    `${replies}/${expectedReplyTotal} replies, ` +
    `post-restart ${postRestartReplies}/${expectedReplyTotal}, ` +
    `post-recover ${postRecoverReplies}/${expectedReplyTotal}${soakLabel}`
  );
}

function liveAgentSessionSmokeStatusMessage(payload) {
  const status = payload.status || "unknown";
  const meetingId = payload.meeting_id || "session-smoke";
  const roundsStatus = payload.rounds_status || "unknown";
  const answeredRounds = Math.max(0, Number(payload.answered_round_count || 0));
  const replies = Math.max(0, Number(payload.reply_count || 0));
  const postRestartReplies = Math.max(0, Number(payload.post_restart_reply_count || 0));
  const postRecoverReplies = Math.max(0, Number(payload.post_recover_reply_count || 0));
  const expectedReplies = Math.max(0, Number(payload.expected_reply_count || 0));
  const lobbyProbeCount = Math.max(1, Number(payload.lobby_probe_count || 1));
  const expectedReplyTotal = expectedReplies * lobbyProbeCount;
  const soakCycles = Math.max(0, Number(payload.soak_cycle_count || 0));
  const soakReplies = Math.max(0, Number(payload.soak_reply_count || 0));
  const expectedSoakReplies = expectedReplies * soakCycles;
  const probeLabel = lobbyProbeCount > 1 ? `${lobbyProbeCount} probes · ` : "";
  const soakLabel = soakCycles > 0 ? ` · soak ${soakReplies}/${expectedSoakReplies} replies over ${soakCycles} cycles` : "";
  return (
    `세션 smoke ${status}: ${meetingId} · ` +
    `rounds ${roundsStatus} (${answeredRounds} answered) · ` +
    probeLabel +
    `${replies}/${expectedReplyTotal} replies · ` +
    `post-restart ${postRestartReplies}/${expectedReplyTotal} replies · ` +
    `post-recover ${postRecoverReplies}/${expectedReplyTotal} replies` +
    soakLabel +
    ` · ` +
    `start ${payload.start_status || "unknown"}, ` +
    `check ${payload.check_status || "unknown"}, ` +
    `resume ${payload.resume_status || "unknown"}, ` +
    `restart ${payload.restart_status || "unknown"}, ` +
    `recover ${payload.recover_status || "unknown"}, ` +
    `stop ${payload.stop_status || "unknown"}`
  );
}

function liveAgentSessionSmokeRequestBody(lobby) {
  const requestBody = { timeout: 12 };
  const soakCycles = liveAgentSessionSmokeSoakCycles(lobby);
  if (soakCycles > 0) {
    requestBody.soak_cycle_count = soakCycles;
    requestBody.soak_interval_seconds = liveAgentSessionSmokeSoakIntervalSeconds(lobby);
  }
  return requestBody;
}

function liveAgentSessionSmokeSoakCycles(lobby) {
  const value = Number(lobby?.querySelector("#live-agent-session-smoke-soak-cycles")?.value || 0);
  if (!Number.isFinite(value)) return 0;
  return Math.min(5, Math.max(0, Math.floor(value)));
}

function liveAgentSessionSmokeSoakIntervalSeconds(lobby) {
  const value = Number(lobby?.querySelector("#live-agent-session-smoke-soak-interval")?.value || 0);
  if (!Number.isFinite(value)) return 0;
  return Math.min(60, Math.max(0, value));
}

async function runLiveAgentPreflight(lobby) {
  if (liveAgentProcessActionBusy()) return;
  const configPath = lobby.querySelector("#live-agent-process-config")?.value.trim() || "";
  if (!configPath) return;
  state.liveAgentPreflightRunning = true;
  state.liveAgentProcessStatus = { message: "상주 config 예비점검 중", tone: "info" };
  renderLobby({ followLatest: false });
  try {
    const payload = await fetchJson("/api/live-agent-preflight", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ config_path: configPath }),
    });
    const tone = payload.status === "ok" ? "success" : "error";
    const summary = payload.summary || {};
    state.liveAgentProcessStatus = { message: `preflight ${payload.status || "unknown"} · ${summary.agents || 0} agents`, tone };
  } catch {
    state.liveAgentProcessStatus = { message: "preflight 예비점검 실패", tone: "error" };
  } finally {
    state.liveAgentPreflightRunning = false;
    await loadLiveAgentOperations({ background: true, force: true });
    renderLobby({ followLatest: false });
  }
}

async function runLiveAgentDiscovery(lobby) {
  if (liveAgentProcessActionBusy()) return;
  const meetingId = lobby.querySelector("#live-agent-session-meeting-id")?.value.trim() || "";
  const includeSessionBundle = lobby.querySelector("#live-agent-discovery-session-bundle")?.checked === true;
  state.liveAgentDiscoveryRunning = true;
  state.liveAgentDiscoveryReport = null;
  state.liveAgentProcessStatus = { message: "CLI 자동 발견 중", tone: "info" };
  renderLobby({ followLatest: false });
  try {
    const payload = await fetchJson("/api/live-agent-discovery", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        meeting_id: meetingId,
        engagement_mode: "mentioned",
        write_config: true,
        session_bundle: includeSessionBundle,
      }),
    });
    state.liveAgentDiscoveryReport = payload;
    const outputPath = applyLiveAgentDiscoveryOutputs(payload);
    const agents = Array.isArray(payload.config?.agents) ? payload.config.agents.length : 0;
    const status = payload.status || "unknown";
    const statusLabel = status === "ok" ? "완료" : status;
    const outputLabel = outputPath ? ` -> ${outputPath}` : "";
    state.liveAgentProcessStatus = {
      message: `CLI 자동 발견 ${statusLabel}: ${agents} agents${outputLabel}`,
      tone: status === "ok" ? "success" : "error",
    };
  } catch (error) {
    state.liveAgentProcessStatus = { message: `CLI 자동 발견 실패: ${error?.message || "알 수 없는 오류"}`, tone: "error" };
  } finally {
    state.liveAgentDiscoveryRunning = false;
    await loadLiveAgentOperations({ background: true, force: true });
    renderLobby({ followLatest: false });
  }
}

async function runLiveAgentAutoJoin(lobby) {
  if (liveAgentProcessActionBusy()) return;
  const meetingId = lobby.querySelector("#live-agent-session-meeting-id")?.value.trim() || "";
  const realProviderApproved = lobby.querySelector("#live-agent-auto-join-real-provider-approval")?.checked === true;
  const approvedAgents = liveAgentDiscoverySelectedApprovals(lobby);
  state.liveAgentAutoJoinRunning = true;
  state.liveAgentDiscoveryReport = null;
  state.liveAgentProcessStatus = { message: "자동입장: CLI 발견 중", tone: "info" };
  renderLobby({ followLatest: false });
  try {
    const discovery = await fetchJson("/api/live-agent-discovery", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        meeting_id: meetingId,
        engagement_mode: "mentioned",
        write_config: true,
        session_bundle: true,
        ...(approvedAgents.length ? { approved_agents: approvedAgents } : {}),
      }),
    });
    state.liveAgentDiscoveryReport = discovery;
    const outputPath = applyLiveAgentDiscoveryOutputs(discovery);
    const agentCount = Array.isArray(discovery.config?.agents) ? discovery.config.agents.length : 0;
    if (discovery.status !== "ok" || !outputPath) {
      state.liveAgentProcessStatus = { message: `자동입장 중단: discovery ${discovery.status || "unknown"} · ${agentCount} agents`, tone: "error" };
      return;
    }
    if (!realProviderApproved && liveAgentDiscoveryRequiresApproval(discovery)) {
      const commands = liveAgentDiscoveryApprovalCommands(discovery);
      const suffix = commands.length ? ` · ${commands.join(", ")}` : "";
      state.liveAgentProcessStatus = { message: `자동입장 중단: 실사용 CLI 승인 필요${suffix}`, tone: "error" };
      return;
    }
    if (!liveAgentDiscoveryHasSessionBundle(discovery)) {
      state.liveAgentProcessStatus = { message: `자동입장 중단: discovery bundle 없음 · ${agentCount} agents`, tone: "error" };
      return;
    }
    state.liveAgentProcessStatus = { message: `자동입장: preflight ${agentCount} agents`, tone: "info" };
    renderLobby({ followLatest: false });

    const preflight = await fetchJson("/api/live-agent-preflight", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ config_path: outputPath }),
    });
    const preflightSummary = preflight.summary || {};
    if (preflight.status !== "ok") {
      state.liveAgentProcessStatus = {
        message: `자동입장 중단: preflight ${preflight.status || "unknown"} · ${preflightSummary.agents || 0} agents`,
        tone: "error",
      };
      return;
    }
    const currentLobby = document.querySelector("#lobby") || lobby;
    await runLiveAgentSessionAction(currentLobby, {
      endpoint: "/api/live-agent-session-runs/ensure",
      includeCouncilConfigs: true,
      busyMessage: "자동입장: 상주 세션런 보장 중",
      failurePrefix: "자동입장 실패",
      notifyRecoverable: true,
      forceProbeBoundAgents: liveAgentDiscoveryHasExactApproval(discovery) || (realProviderApproved && liveAgentDiscoveryRequiresApproval(discovery)),
      approveRealProviders: liveAgentDiscoveryHasExactApproval(discovery) || (realProviderApproved && liveAgentDiscoveryRequiresApproval(discovery)),
    });
  } catch (error) {
    state.liveAgentProcessStatus = { message: `자동입장 실패: ${error?.message || "알 수 없는 오류"}`, tone: "error" };
  } finally {
    state.liveAgentAutoJoinRunning = false;
    await loadLiveAgentOperations({ background: true, force: true });
    renderLobby({ followLatest: false });
  }
}

function liveAgentDiscoveryRequiresApproval(discovery) {
  const discoveries = Array.isArray(discovery?.discoveries) ? discovery.discoveries : [];
  return discoveries.some((item) => item?.included && item?.requires_approval && item?.approval_status !== "approved");
}

function liveAgentDiscoveryApprovalCommands(discovery) {
  const discoveries = Array.isArray(discovery?.discoveries) ? discovery.discoveries : [];
  return discoveries
    .filter((item) => item?.included && item?.requires_approval)
    .map((item) => String(item?.command || "").trim())
    .filter(Boolean)
    .slice(0, 5);
}

function liveAgentDiscoveryHasExactApproval(discovery) {
  const approvalFilter = discovery?.approval_filter && typeof discovery.approval_filter === "object" ? discovery.approval_filter : {};
  return Number(approvalFilter.approved_count || 0) > 0;
}

function liveAgentDiscoverySelectedApprovals(root) {
  return Array.from(root.querySelectorAll("[data-live-agent-discovery-approve-agent]"))
    .filter((input) => input.checked)
    .map((input) => String(input.dataset?.liveAgentDiscoveryApproveAgent || "").trim())
    .filter(liveAgentDiscoverySafeApprovalAgentId)
    .slice(0, 32);
}

function restoreLiveAgentDiscoverySelectedApprovals(root, approvedAgents) {
  const approved = new Set(Array.isArray(approvedAgents) ? approvedAgents.map((value) => String(value || "").trim()).filter(liveAgentDiscoverySafeApprovalAgentId) : []);
  root.querySelectorAll("[data-live-agent-discovery-approve-agent]").forEach((input) => {
    input.checked = approved.has(String(input.dataset?.liveAgentDiscoveryApproveAgent || "").trim());
  });
}

function liveAgentDiscoverySafeApprovalAgentId(value) {
  return /^[A-Za-z0-9_.:-]{1,64}$/.test(String(value || "").trim());
}

function liveAgentDiscoveryHasSessionBundle(discovery) {
  const sessionBundle = discovery?.session_bundle && typeof discovery.session_bundle === "object" ? discovery.session_bundle : {};
  return Boolean(
    sessionBundle.live_agent_config_path &&
      sessionBundle.group_id &&
      sessionBundle.council_config_path &&
      sessionBundle.agent_config_path
  );
}

function applyLiveAgentDiscoveryOutputs(discovery) {
  const sessionBundle = discovery?.session_bundle && typeof discovery.session_bundle === "object" ? discovery.session_bundle : {};
  const outputPath = String(sessionBundle.live_agent_config_path || discovery?.output || "");
  const configInput = document.querySelector("#live-agent-process-config");
  const groupInput = document.querySelector("#live-agent-process-group");
  const councilInput = document.querySelector("#live-agent-session-council-config");
  const agentInput = document.querySelector("#live-agent-session-agent-config");
  if (outputPath && configInput) configInput.value = outputPath;
  if (sessionBundle.group_id && groupInput) groupInput.value = String(sessionBundle.group_id);
  if (sessionBundle.council_config_path && councilInput) councilInput.value = String(sessionBundle.council_config_path);
  if (sessionBundle.agent_config_path && agentInput) agentInput.value = String(sessionBundle.agent_config_path);
  return outputPath;
}

async function startLiveAgentProcessGroup(form) {
  if (liveAgentProcessActionBusy()) return;
  const configPath = form.querySelector("#live-agent-process-config")?.value.trim() || "";
  const groupId = form.querySelector("#live-agent-process-group")?.value.trim() || "";
  const autoRestart = Boolean(form.querySelector("#live-agent-process-auto-restart")?.checked);
  const maxRestarts = Math.max(0, Number(form.querySelector("#live-agent-process-max-restarts")?.value || 0));
  const restartBackoffSeconds = Math.max(0, Number(form.querySelector("#live-agent-process-restart-backoff")?.value || 0));
  const staleRestartAfterSeconds = Math.max(0, Number(form.querySelector("#live-agent-process-stale-restart-after")?.value || 0));
  if (!configPath) return;
  state.liveAgentProcessStartRunning = true;
  state.liveAgentProcessStatus = { message: "상주 그룹 시작 중", tone: "info" };
  renderLobby({ followLatest: false });
  try {
    const payload = await fetchJson("/api/live-agent-processes/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        config_path: configPath,
        group_id: groupId,
        auto_restart: autoRestart,
        max_restarts: maxRestarts,
        restart_backoff_seconds: restartBackoffSeconds,
        stale_restart_after_seconds: staleRestartAfterSeconds,
      }),
    });
    setLiveAgentProcesses(payload.groups || []);
    state.liveAgentProcessesLoaded = true;
    state.liveAgentProcessStatus = { message: `${payload.group?.group_id || "live-agents"} 시작됨`, tone: "success" };
  } catch (error) {
    state.liveAgentProcessStatus = { message: `상주 그룹 시작 실패: ${error?.message || "알 수 없는 오류"}`, tone: "error" };
  } finally {
    state.liveAgentProcessStartRunning = false;
    await refreshLiveAgentProcessHistory();
    renderLobby({ followLatest: false });
  }
}

async function startLiveAgentSession(lobby) {
  if (liveAgentProcessActionBusy()) return;
  await runLiveAgentSessionAction(lobby, {
    endpoint: "/api/live-agent-sessions/start",
    includeCouncilConfigs: true,
    busyMessage: "상주 세션 시작 중",
    failurePrefix: "상주 세션 시작 실패",
    notifyRecoverable: true,
  });
}

async function ensureLiveAgentSession(lobby) {
  if (liveAgentProcessActionBusy()) return;
  await runLiveAgentSessionAction(lobby, {
    endpoint: "/api/live-agent-sessions/ensure",
    includeCouncilConfigs: true,
    busyMessage: "상주 세션 보장 중",
    failurePrefix: "상주 세션 보장 실패",
    notifyRecoverable: true,
  });
}

async function ensureLiveAgentSessionRun(lobby) {
  if (liveAgentProcessActionBusy()) return;
  await runLiveAgentSessionAction(lobby, {
    endpoint: "/api/live-agent-session-runs/ensure",
    includeCouncilConfigs: true,
    busyMessage: "상주 세션런 보장 중",
    failurePrefix: "상주 세션런 보장 실패",
    notifyRecoverable: true,
  });
}

async function resumeLiveAgentSession(lobby) {
  if (liveAgentProcessActionBusy()) return;
  await runLiveAgentSessionAction(lobby, {
    endpoint: "/api/live-agent-sessions/resume",
    includeCouncilConfigs: false,
    busyMessage: "상주 세션 재개 중",
    failurePrefix: "상주 세션 재개 실패",
    notifyRecoverable: false,
  });
}

async function restartLiveAgentSession(lobby) {
  if (liveAgentProcessActionBusy()) return;
  const meetingId = lobby.querySelector("#live-agent-session-meeting-id")?.value.trim() || "";
  const groupId = lobby.querySelector("#live-agent-process-group")?.value.trim() || "";
  if (!meetingId || !groupId) return;
  state.liveAgentSessionRestartRunning = true;
  state.liveAgentProcessStatus = { message: "상주 세션 재시작 중", tone: "info" };
  renderLobby({ followLatest: false });
  try {
    const requestBody = {
      meeting_id: meetingId,
      group_id: groupId,
      connect_timeout_seconds: liveAgentSessionConnectTimeoutSeconds(lobby),
    };
    addLiveAgentSessionRemainingRoundsPayload(lobby, requestBody);
    const payload = await fetchJson("/api/live-agent-sessions/restart", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(requestBody),
    });
    await refreshLiveAgentRuntimeSurfaces();
    notifyMeetingStarted(payload.meeting_id);
    state.liveAgentProcessStatus = { message: liveAgentSessionStatusMessage(payload), tone: liveAgentSessionStatusTone(payload) };
  } catch (error) {
    state.liveAgentProcessStatus = { message: `상주 세션 재시작 실패: ${error?.message || "알 수 없는 오류"}`, tone: "error" };
  } finally {
    state.liveAgentSessionRestartRunning = false;
    await loadLiveAgentOperations({ background: true, force: true });
    renderLobby({ followLatest: false });
  }
}

async function recoverLiveAgentSession(lobby) {
  if (liveAgentProcessActionBusy()) return;
  const meetingId = lobby.querySelector("#live-agent-session-meeting-id")?.value.trim() || "";
  const groupId = lobby.querySelector("#live-agent-process-group")?.value.trim() || "";
  if (!meetingId || !groupId) return;
  state.liveAgentSessionRecoverRunning = true;
  state.liveAgentProcessStatus = { message: "상주 세션 복구 중", tone: "info" };
  renderLobby({ followLatest: false });
  try {
    const requestBody = {
      meeting_id: meetingId,
      group_id: groupId,
      connect_timeout_seconds: liveAgentSessionConnectTimeoutSeconds(lobby),
    };
    addLiveAgentSessionRemainingRoundsPayload(lobby, requestBody);
    const payload = await fetchJson("/api/live-agent-sessions/recover", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(requestBody),
    });
    await refreshLiveAgentRuntimeSurfaces();
    notifyMeetingStarted(payload.meeting_id);
    state.liveAgentProcessStatus = { message: liveAgentSessionStatusMessage(payload), tone: liveAgentSessionStatusTone(payload) };
  } catch (error) {
    state.liveAgentProcessStatus = { message: `상주 세션 복구 실패: ${error?.message || "알 수 없는 오류"}`, tone: "error" };
  } finally {
    state.liveAgentSessionRecoverRunning = false;
    await loadLiveAgentOperations({ background: true, force: true });
    renderLobby({ followLatest: false });
  }
}

function addLiveAgentSessionRemainingRoundsPayload(lobby, requestBody) {
  addLiveAgentSessionProbePayload(lobby, requestBody);
  const runRemainingRounds = lobby.querySelector("#live-agent-session-run-remaining-rounds")?.checked === true;
  if (!runRemainingRounds) return;
  requestBody.run_remaining_rounds = true;
  requestBody.round_timeout_seconds = liveAgentRoundTimeoutSeconds(lobby);
  requestBody.round_max_rounds = liveAgentRoundMaxRounds(lobby);
  requestBody.round_stop_on_timeout = lobby.querySelector("#live-agent-round-stop-on-timeout")?.checked === true;
}

function addLiveAgentSessionProbePayload(lobby, requestBody, options = {}) {
  const probeBoundAgents = options.force === true || lobby.querySelector("#live-agent-session-probe-bound-agents")?.checked === true;
  if (!probeBoundAgents) return;
  requestBody.probe_bound_agents = true;
  requestBody.probe_timeout_seconds = liveAgentSessionProbeTimeoutSeconds(lobby);
}

async function checkLiveAgentSession(lobby) {
  if (liveAgentProcessActionBusy()) return;
  const meetingId = lobby.querySelector("#live-agent-session-meeting-id")?.value.trim() || "";
  const groupId = lobby.querySelector("#live-agent-process-group")?.value.trim() || "";
  if (!meetingId || !groupId) return;
  state.liveAgentSessionCheckRunning = true;
  state.liveAgentProcessStatus = { message: "상주 세션 점검 중", tone: "info" };
  renderLobby({ followLatest: false });
  try {
    const payload = await fetchJson("/api/live-agent-sessions/check", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ meeting_id: meetingId, group_id: groupId }),
    });
    state.liveAgentProcessStatus = { message: liveAgentSessionCheckStatusMessage(payload), tone: liveAgentSessionStatusTone(payload) };
  } catch (error) {
    state.liveAgentProcessStatus = { message: `상주 세션 점검 실패: ${error?.message || "알 수 없는 오류"}`, tone: "error" };
  } finally {
    state.liveAgentSessionCheckRunning = false;
    await loadLiveAgentOperations({ background: true, force: true });
    renderLobby({ followLatest: false });
  }
}

async function stopLiveAgentSession(lobby) {
  if (liveAgentProcessActionBusy()) return;
  const meetingId = lobby.querySelector("#live-agent-session-meeting-id")?.value.trim() || "";
  const groupId = lobby.querySelector("#live-agent-process-group")?.value.trim() || "";
  if (!meetingId || !groupId) return;
  state.liveAgentSessionStopRunning = true;
  state.liveAgentProcessStatus = { message: "상주 세션 중지 중", tone: "info" };
  renderLobby({ followLatest: false });
  try {
    const payload = await fetchJson("/api/live-agent-sessions/stop", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ meeting_id: meetingId, group_id: groupId }),
    });
    await refreshLiveAgentRuntimeSurfaces();
    state.liveAgentProcessStatus = { message: liveAgentSessionStopStatusMessage(payload), tone: liveAgentSessionStatusTone(payload) };
  } catch (error) {
    state.liveAgentProcessStatus = { message: `상주 세션 중지 실패: ${error?.message || "알 수 없는 오류"}`, tone: "error" };
  } finally {
    state.liveAgentSessionStopRunning = false;
    await loadLiveAgentOperations({ background: true, force: true });
    renderLobby({ followLatest: false });
  }
}

async function startLiveAgentFlow(lobby) {
  if (liveAgentProcessActionBusy()) return;
  const meetingId = lobby.querySelector("#live-agent-session-meeting-id")?.value.trim() || "";
  const topic = lobby.querySelector("#live-agent-flow-topic")?.value.trim() || state.payload?.meeting?.display_topic || state.payload?.meeting?.topic || "";
  const durationSeconds = Math.max(1, Number(lobby.querySelector("#live-agent-flow-duration")?.value || 180));
  if (!meetingId || !topic) {
    state.liveAgentProcessStatus = { message: "자유토론 시작 실패: meeting id와 topic이 필요합니다", tone: "error" };
    renderLobby({ followLatest: false });
    return;
  }
  state.liveAgentFlowStartRunning = true;
  state.liveAgentProcessStatus = { message: "Play Mode 자유토론 시작 중", tone: "info" };
  renderLobby({ followLatest: false });
  try {
    const payload = await fetchJson("/api/live-agent-flow/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        meeting_id: meetingId,
        topic,
        duration_seconds: durationSeconds,
      }),
    });
    state.liveAgentFlow = payload.flow || { status: "idle" };
    state.liveAgentFlowEvents = payload.flow_events || [];
    state.liveAgentFlowLoaded = true;
    setLobbyEvents(payload.events || state.lobbyEvents);
    state.liveAgentProcessStatus = { message: `자유토론 ${state.liveAgentFlow.status || "running"}`, tone: "success" };
    notifyLiveAgentFlowUpdated();
  } catch (error) {
    state.liveAgentProcessStatus = { message: `자유토론 시작 실패: ${error?.message || "알 수 없는 오류"}`, tone: "error" };
  } finally {
    state.liveAgentFlowStartRunning = false;
    await refreshLiveAgentProcessHistory();
    await loadLiveAgents({ background: true, force: true });
    renderLobby({ followLatest: false });
  }
}

async function stopLiveAgentFlow(lobby) {
  if (liveAgentProcessActionBusy()) return;
  const meetingId = lobby.querySelector("#live-agent-session-meeting-id")?.value.trim() || "";
  if (!meetingId) return;
  state.liveAgentFlowStopRunning = true;
  state.liveAgentProcessStatus = { message: "Play Mode 자유토론 중지 중", tone: "info" };
  renderLobby({ followLatest: false });
  try {
    const payload = await fetchJson("/api/live-agent-flow/stop", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ meeting_id: meetingId }),
    });
    state.liveAgentFlow = payload.flow || { status: "idle" };
    state.liveAgentFlowEvents = payload.flow_events || [];
    state.liveAgentFlowLoaded = true;
    setLobbyEvents(payload.events || state.lobbyEvents);
    state.liveAgentProcessStatus = { message: `자유토론 ${state.liveAgentFlow.status || "stopped"}`, tone: "success" };
    notifyLiveAgentFlowUpdated();
  } catch (error) {
    state.liveAgentProcessStatus = { message: `자유토론 중지 실패: ${error?.message || "알 수 없는 오류"}`, tone: "error" };
  } finally {
    state.liveAgentFlowStopRunning = false;
    await refreshLiveAgentProcessHistory();
    await loadLiveAgents({ background: true, force: true });
    renderLobby({ followLatest: false });
  }
}

function notifyLiveAgentFlowUpdated() {
  if (typeof window === "undefined" || typeof window.dispatchEvent !== "function" || typeof CustomEvent === "undefined") return;
  window.dispatchEvent(new CustomEvent("agentsassemble:live-agent-flow-updated"));
}

async function stopRunningLiveAgentProcessGroups() {
  if (liveAgentProcessActionBusy()) return;
  state.liveAgentProcessBulkStopRunning = true;
  state.liveAgentProcessStatus = { message: "실행 중인 상주 그룹 중지 중", tone: "info" };
  renderLobby({ followLatest: false });
  try {
    const payload = await fetchJson("/api/live-agent-processes/stop-running", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    setLiveAgentProcesses(payload.groups || []);
    state.liveAgentProcessesLoaded = true;
    const result = payload.result && typeof payload.result === "object" ? payload.result : {};
    const stopped = Math.max(0, Number(result.stopped_count || 0));
    const failed = Math.max(0, Number(result.failed_count || 0));
    const skipped = Math.max(0, Number(result.skipped_count || 0));
    const suffix = `${failed ? ` · failed ${failed}` : ""}${skipped ? ` · skipped ${skipped}` : ""}`;
    state.liveAgentProcessStatus = { message: `실행 그룹 ${stopped}개 중지됨${suffix}`, tone: failed ? "error" : "success" };
  } catch (error) {
    state.liveAgentProcessStatus = { message: `실행 그룹 중지 실패: ${error?.message || "알 수 없는 오류"}`, tone: "error" };
  } finally {
    await refreshLiveAgentProcessHistory();
    state.liveAgentProcessBulkStopRunning = false;
    renderLobby({ followLatest: false });
  }
}

async function runLiveAgentSessionAction(
  lobby,
  { endpoint, includeCouncilConfigs, busyMessage, failurePrefix, notifyRecoverable, forceProbeBoundAgents = false, approveRealProviders = false }
) {
  const liveAgentConfigPath = lobby.querySelector("#live-agent-process-config")?.value.trim() || "";
  const groupId = lobby.querySelector("#live-agent-process-group")?.value.trim() || "";
  const meetingId = lobby.querySelector("#live-agent-session-meeting-id")?.value.trim() || "";
  const councilConfigPath = lobby.querySelector("#live-agent-session-council-config")?.value.trim() || "";
  const agentConfigPath = lobby.querySelector("#live-agent-session-agent-config")?.value.trim() || "";
  const connectTimeoutSeconds = liveAgentSessionConnectTimeoutSeconds(lobby);
  const runRemainingRounds = lobby.querySelector("#live-agent-session-run-remaining-rounds")?.checked === true;
  const roundTimeoutSeconds = liveAgentRoundTimeoutSeconds(lobby);
  const roundMaxRounds = liveAgentRoundMaxRounds(lobby);
  const roundStopOnTimeout = lobby.querySelector("#live-agent-round-stop-on-timeout")?.checked === true;
  const autoRestart = Boolean(lobby.querySelector("#live-agent-process-auto-restart")?.checked);
  const maxRestarts = Math.max(0, Number(lobby.querySelector("#live-agent-process-max-restarts")?.value || 0));
  const restartBackoffSeconds = Math.max(0, Number(lobby.querySelector("#live-agent-process-restart-backoff")?.value || 0));
  const staleRestartAfterSeconds = Math.max(0, Number(lobby.querySelector("#live-agent-process-stale-restart-after")?.value || 0));
  if (!liveAgentConfigPath) return;
  state.liveAgentSessionStartRunning = true;
  state.liveAgentProcessStatus = { message: busyMessage, tone: "info" };
  renderLobby({ followLatest: false });
  try {
    const requestBody = {
      meeting_id: meetingId,
      group_id: groupId,
      live_agent_config_path: liveAgentConfigPath,
      connect_timeout_seconds: connectTimeoutSeconds,
      auto_restart: autoRestart,
      max_restarts: maxRestarts,
      restart_backoff_seconds: restartBackoffSeconds,
      stale_restart_after_seconds: staleRestartAfterSeconds,
    };
    if (includeCouncilConfigs) {
      requestBody.council_config_path = councilConfigPath;
      requestBody.agent_config_path = agentConfigPath;
    }
    if (approveRealProviders) {
      requestBody.approve_real_providers = true;
    }
    addLiveAgentSessionProbePayload(lobby, requestBody, { force: forceProbeBoundAgents });
    if (runRemainingRounds) {
      requestBody.run_remaining_rounds = true;
      requestBody.round_timeout_seconds = roundTimeoutSeconds;
      requestBody.round_max_rounds = roundMaxRounds;
      requestBody.round_stop_on_timeout = roundStopOnTimeout;
    }
    const payload = await fetchJson(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(requestBody),
    });
    await refreshLiveAgentRuntimeSurfaces();
    notifyMeetingStarted(payload.meeting_id);
    state.liveAgentProcessStatus = { message: liveAgentSessionStatusMessage(payload), tone: liveAgentSessionStatusTone(payload) };
  } catch (error) {
    if (notifyRecoverable) notifyRecoverableSessionMeeting(error);
    state.liveAgentProcessStatus = { message: `${failurePrefix}: ${error?.message || "알 수 없는 오류"}`, tone: "error" };
  } finally {
    state.liveAgentSessionStartRunning = false;
    await loadLiveAgentOperations({ background: true, force: true });
    renderLobby({ followLatest: false });
  }
}

async function callLiveAgentOfficialRound(lobby) {
  if (liveAgentProcessActionBusy()) return;
  const meetingId = liveAgentOfficialRoundMeetingId(lobby);
  const roundId = lobby.querySelector("#live-agent-round-id")?.value.trim() || "";
  if (!meetingId || !roundId) {
    state.liveAgentProcessStatus = { message: "공식 라운드 호출 실패: meeting id와 round id가 필요합니다", tone: "error" };
    renderLobby({ followLatest: false });
    return;
  }
  const timeoutSeconds = liveAgentRoundTimeoutSeconds(lobby);
  const stopOnTimeout = lobby.querySelector("#live-agent-round-stop-on-timeout")?.checked === true;
  state.liveAgentRoundCallRunning = true;
  state.liveAgentProcessStatus = { message: "공식 라운드 호출 중", tone: "info" };
  renderLobby({ followLatest: false });
  try {
    const payload = await fetchJson(`/api/meetings/${encodeURIComponent(meetingId)}/live-agent-turns/round`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        round_id: roundId,
        timeout_seconds: timeoutSeconds,
        stop_on_timeout: stopOnTimeout,
      }),
    });
    const tone = payload.status === "answered" || payload.status === "complete" ? "success" : "error";
    state.liveAgentProcessStatus = { message: liveAgentRoundStatusMessage(payload), tone };
    notifyMeetingRefreshRequested(meetingId);
  } catch (error) {
    state.liveAgentProcessStatus = { message: `공식 라운드 호출 실패: ${error?.message || "알 수 없는 오류"}`, tone: "error" };
  } finally {
    state.liveAgentRoundCallRunning = false;
    await loadLiveAgentOperations({ background: true, force: true });
    renderLobby({ followLatest: false });
  }
}

async function callLiveAgentRemainingRounds(lobby) {
  if (liveAgentProcessActionBusy()) return;
  const meetingId = liveAgentOfficialRoundMeetingId(lobby);
  if (!meetingId) {
    state.liveAgentProcessStatus = { message: "남은 공식 라운드 호출 실패: meeting id가 필요합니다", tone: "error" };
    renderLobby({ followLatest: false });
    return;
  }
  const timeoutSeconds = liveAgentRoundTimeoutSeconds(lobby);
  const stopOnTimeout = lobby.querySelector("#live-agent-round-stop-on-timeout")?.checked === true;
  const maxRounds = liveAgentRoundMaxRounds(lobby);
  state.liveAgentRoundCallRunning = true;
  state.liveAgentProcessStatus = { message: "남은 공식 라운드 호출 중", tone: "info" };
  renderLobby({ followLatest: false });
  try {
    const payload = await fetchJson(`/api/meetings/${encodeURIComponent(meetingId)}/live-agent-turns/rounds`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        timeout_seconds: timeoutSeconds,
        stop_on_timeout: stopOnTimeout,
        max_rounds: maxRounds,
      }),
    });
    const tone = payload.status === "answered" || payload.status === "complete" ? "success" : "error";
    state.liveAgentProcessStatus = { message: liveAgentRemainingRoundsStatusMessage(payload), tone };
    notifyMeetingRefreshRequested(meetingId);
  } catch (error) {
    state.liveAgentProcessStatus = { message: `남은 공식 라운드 호출 실패: ${error?.message || "알 수 없는 오류"}`, tone: "error" };
  } finally {
    state.liveAgentRoundCallRunning = false;
    await loadLiveAgentOperations({ background: true, force: true });
    renderLobby({ followLatest: false });
  }
}

async function callLiveAgentReviewCheckpoint(lobby) {
  if (liveAgentProcessActionBusy()) return;
  const meetingId = liveAgentOfficialRoundMeetingId(lobby);
  const groupId = lobby.querySelector("#live-agent-process-group")?.value.trim() || "";
  const content = lobby.querySelector("#live-agent-review-checkpoint-message")?.value.trim() || "";
  if (!meetingId || !groupId || !content) {
    state.liveAgentProcessStatus = { message: "리뷰 checkpoint 요청 실패: meeting id, group id, 메시지가 필요합니다", tone: "error" };
    renderLobby({ followLatest: false });
    return;
  }
  state.liveAgentReviewCheckpointRunning = true;
  state.liveAgentProcessStatus = { message: "리뷰 checkpoint 요청 중", tone: "info" };
  renderLobby({ followLatest: false });
  try {
    const requestBody = {
      group_id: groupId,
      content,
      timeout_seconds: liveAgentReviewCheckpointTimeoutSeconds(lobby),
    };
    const checkpointId = lobby.querySelector("#live-agent-review-checkpoint-id")?.value.trim() || "";
    if (checkpointId) requestBody.checkpoint_id = checkpointId;
    const payload = await fetchJson(`/api/meetings/${encodeURIComponent(meetingId)}/review-checkpoints`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(requestBody),
    });
    const tone = payload.status === "answered" ? "success" : "error";
    state.liveAgentProcessStatus = { message: liveAgentReviewCheckpointStatusMessage(payload), tone };
    notifyMeetingRefreshRequested(meetingId);
  } catch (error) {
    state.liveAgentProcessStatus = { message: `리뷰 checkpoint 요청 실패: ${error?.message || "알 수 없는 오류"}`, tone: "error" };
  } finally {
    state.liveAgentReviewCheckpointRunning = false;
    await loadLiveAgentOperations({ background: true, force: true });
    renderLobby({ followLatest: false });
  }
}

function liveAgentOfficialRoundMeetingId(lobby) {
  return lobby.querySelector("#live-agent-session-meeting-id")?.value.trim() || "";
}

function liveAgentRoundTimeoutSeconds(lobby) {
  const value = Number(lobby.querySelector("#live-agent-round-timeout")?.value || 30);
  if (!Number.isFinite(value)) return 30;
  return Math.min(600, Math.max(0, value));
}

function liveAgentRoundMaxRounds(lobby) {
  const value = Number(lobby.querySelector("#live-agent-round-max-rounds")?.value || 8);
  if (!Number.isFinite(value)) return 8;
  return Math.min(8, Math.max(1, value));
}

function liveAgentReviewCheckpointTimeoutSeconds(lobby) {
  const value = Number(lobby.querySelector("#live-agent-review-checkpoint-timeout")?.value || 30);
  if (!Number.isFinite(value)) return 30;
  return Math.min(600, Math.max(0, value));
}

function liveAgentRoundStatusMessage(payload) {
  const status = payload.status || "unknown";
  const roundId = payload.round_id || "round";
  const answered = Math.max(0, Number(payload.answered_count || 0));
  const timedOut = Math.max(0, Number(payload.timeout_count || 0));
  const skipped = Math.max(0, Number(payload.skipped_count || 0));
  return `공식 라운드 ${status}: ${roundId} · ${answered} answered, ${timedOut} timed out, ${skipped} skipped`;
}

function liveAgentRemainingRoundsStatusMessage(payload) {
  const status = payload.status || "unknown";
  const roundCount = Math.max(0, Number(payload.round_count || 0));
  const answered = Math.max(0, Number(payload.answered_round_count || 0));
  const completed = Math.max(0, Number(payload.completed_round_count || 0));
  const timedOut = Math.max(0, Number(payload.timeout_round_count || 0));
  const skipped = Math.max(0, Number(payload.skipped_round_count || 0));
  const completedText = completed ? `, ${completed} already complete` : "";
  return `남은 공식 라운드 ${status}: ${roundCount} rounds · ${answered} answered${completedText}, ${timedOut} timed out, ${skipped} skipped`;
}

function liveAgentReviewCheckpointStatusMessage(payload) {
  const status = payload.status || "unknown";
  const checkpointId = payload.checkpoint_id || "checkpoint";
  const turnCount = Math.max(0, Number(payload.turn_count || 0));
  const answered = Math.max(0, Number(payload.answered_count || 0));
  const timedOut = Math.max(0, Number(payload.timeout_count || 0));
  const skipped = Math.max(0, Number(payload.skipped_count || 0));
  return `리뷰 checkpoint ${status}: ${checkpointId} · ${answered}/${turnCount} answered, ${timedOut} timed out, ${skipped} skipped`;
}

function liveAgentSessionConnectTimeoutSeconds(lobby) {
  const value = Number(lobby.querySelector("#live-agent-session-connect-timeout")?.value || 5);
  if (!Number.isFinite(value)) return 5;
  return Math.min(120, Math.max(0, value));
}

function liveAgentSessionProbeTimeoutSeconds(lobby) {
  const value = Number(lobby.querySelector("#live-agent-session-probe-timeout")?.value || 12);
  if (!Number.isFinite(value)) return 12;
  return Math.min(240, Math.max(0, value));
}

function liveAgentSessionStatusMessage(payload) {
  const status = payload.status || "unknown";
  const meetingId = payload.meeting_id || "meeting";
  const connection = payload.connection && typeof payload.connection === "object" ? payload.connection : {};
  const expected = Math.max(0, Number(connection.expected || 0));
  const connected = Math.max(0, Number(connection.connected || 0));
  const replyProbe = liveAgentSessionReplyProbeLabel(payload);
  const autoRounds = liveAgentSessionAutoRoundsLabel(payload);
  const sessionRun = liveAgentSessionRunResultLabel(payload);
  return `세션 ${status}: ${meetingId} · ${connected}/${expected} connected${replyProbe ? ` · ${replyProbe}` : ""}${autoRounds ? ` · ${autoRounds}` : ""}${sessionRun ? ` · ${sessionRun}` : ""}`;
}

function liveAgentSessionStopStatusMessage(payload) {
  const offline = payload?.offline && typeof payload.offline === "object" ? payload.offline : {};
  const sessionRuns = liveAgentStoppedSessionRunsLabel(payload);
  return `세션 ${payload?.status || "unknown"}: ${payload?.meeting_id || "unknown"} · ${payload?.group_id || "unknown"} · ${offline.offline || 0}/${offline.expected || 0} offline${sessionRuns ? ` · ${sessionRuns}` : ""}`;
}

function liveAgentStoppedSessionRunsLabel(payload) {
  const runs = Array.isArray(payload?.session_runs) ? payload.session_runs : [];
  const stopped = runs.filter((run) => run && typeof run === "object" && run.status === "stopped");
  return stopped.length ? `runs stopped ${stopped.length}` : "";
}

function liveAgentSessionCheckStatusMessage(payload) {
  const connection = payload?.connection && typeof payload.connection === "object" ? payload.connection : {};
  const process = payload?.process && typeof payload.process === "object" ? payload.process : {};
  const expected = Math.max(0, Number(connection.expected || 0));
  const connected = Math.max(0, Number(connection.connected || 0));
  return `세션 ${payload?.status || "unknown"}: ${payload?.meeting_id || "unknown"} · ${payload?.group_id || "unknown"} · ${connected}/${expected} connected · process ${process.status || "unknown"}`;
}

function liveAgentSessionStatusTone(payload) {
  if (payload.status === "stopped") return "success";
  if (payload.status !== "ready") return "info";
  const replyProbe = payload.reply_probe && typeof payload.reply_probe === "object" ? payload.reply_probe : null;
  if (replyProbe && replyProbe.status !== "ok") return "error";
  const autoRounds = payload.auto_rounds && typeof payload.auto_rounds === "object" ? payload.auto_rounds : null;
  if (!autoRounds) return "success";
  return autoRounds.status === "answered" || autoRounds.status === "complete" ? "success" : "error";
}

function liveAgentSessionReplyProbeLabel(payload) {
  const replyProbe = payload.reply_probe && typeof payload.reply_probe === "object" ? payload.reply_probe : null;
  if (!replyProbe) return "";
  const status = replyProbe.status || "unknown";
  const probeCount = Math.max(0, Number(replyProbe.probe_count || 0));
  const okCount = Math.max(0, Number(replyProbe.ok_count || 0));
  return `probes ${status}: ${okCount}/${probeCount} ok`;
}

function liveAgentSessionAutoRoundsLabel(payload) {
  const autoRounds = payload.auto_rounds && typeof payload.auto_rounds === "object" ? payload.auto_rounds : null;
  if (!autoRounds) return "";
  const status = autoRounds.status || "unknown";
  const roundCount = Math.max(0, Number(autoRounds.round_count || 0));
  const answered = Math.max(0, Number(autoRounds.answered_round_count || 0));
  const completed = Math.max(0, Number(autoRounds.completed_round_count || 0));
  const timedOut = Math.max(0, Number(autoRounds.timeout_round_count || 0));
  const skipped = Math.max(0, Number(autoRounds.skipped_round_count || 0));
  const completedText = completed ? `, ${completed} already complete` : "";
  return `rounds ${status}: ${roundCount} rounds, ${answered} answered${completedText}, ${timedOut} timed out, ${skipped} skipped`;
}

function liveAgentSessionRunResultLabel(payload) {
  const run = payload?.session_run && typeof payload.session_run === "object" ? payload.session_run : null;
  if (!run) return "";
  return `run ${run.run_id || "-"} ${run.status || "unknown"}`;
}

function notifyRecoverableSessionMeeting(error) {
  const payload = error?.payload && typeof error.payload === "object" ? error.payload : {};
  const details = payload.details && typeof payload.details === "object" ? payload.details : {};
  notifyMeetingStarted(payload.recoverable_meeting_id || details.recoverable_meeting_id);
}

function notifyMeetingStarted(meetingId) {
  if (!meetingId || typeof globalThis.dispatchEvent !== "function" || typeof globalThis.CustomEvent !== "function") {
    return;
  }
  globalThis.dispatchEvent(new CustomEvent("agentsassemble:meeting-started", { detail: { meetingId } }));
}

function notifyMeetingRefreshRequested(meetingId) {
  if (!meetingId || typeof globalThis.dispatchEvent !== "function" || typeof globalThis.CustomEvent !== "function") {
    return;
  }
  globalThis.dispatchEvent(new CustomEvent("agentsassemble:meeting-refresh-requested", { detail: { meetingId } }));
}

async function stopLiveAgentProcessGroup(groupId) {
  if (!groupId || liveAgentProcessActionBusy()) return;
  state.liveAgentProcessRowActionRunning = groupId;
  state.liveAgentProcessStatus = { message: `${groupId} 중지 중`, tone: "info" };
  renderLobby({ followLatest: false });
  try {
    const payload = await fetchJson(`/api/live-agent-processes/${encodeURIComponent(groupId)}/stop`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    setLiveAgentProcesses(payload.groups || []);
    state.liveAgentProcessesLoaded = true;
    state.liveAgentProcessStatus = { message: `${groupId} 중지됨`, tone: "success" };
  } catch {
    state.liveAgentProcessStatus = { message: `${groupId} 중지 실패`, tone: "error" };
  } finally {
    await refreshLiveAgentProcessHistory();
    state.liveAgentProcessRowActionRunning = "";
    renderLobby({ followLatest: false });
  }
}

async function restartLiveAgentProcessGroup(groupId) {
  if (!groupId || liveAgentProcessActionBusy()) return;
  state.liveAgentProcessRowActionRunning = groupId;
  state.liveAgentProcessStatus = { message: `${groupId} 재시작 중`, tone: "info" };
  renderLobby({ followLatest: false });
  try {
    const payload = await fetchJson(`/api/live-agent-processes/${encodeURIComponent(groupId)}/restart`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    setLiveAgentProcesses(payload.groups || []);
    state.liveAgentProcessesLoaded = true;
    state.liveAgentProcessStatus = { message: `${groupId} 재시작됨`, tone: "success" };
  } catch (error) {
    state.liveAgentProcessStatus = { message: `${groupId} 재시작 실패: ${error?.message || "알 수 없는 오류"}`, tone: "error" };
  } finally {
    await refreshLiveAgentProcessHistory();
    state.liveAgentProcessRowActionRunning = "";
    renderLobby({ followLatest: false });
  }
}

async function recoverLiveAgentProcessGroup(groupId) {
  if (!groupId || liveAgentProcessActionBusy()) return;
  state.liveAgentProcessRowActionRunning = groupId;
  state.liveAgentProcessStatus = { message: `${groupId} 복구 중`, tone: "info" };
  renderLobby({ followLatest: false });
  try {
    const payload = await fetchJson(`/api/live-agent-processes/${encodeURIComponent(groupId)}/recover`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    setLiveAgentProcesses(payload.groups || []);
    state.liveAgentProcessesLoaded = true;
    state.liveAgentProcessStatus = { message: `${groupId} 복구됨`, tone: "success" };
  } catch (error) {
    state.liveAgentProcessStatus = { message: `${groupId} 복구 실패: ${error?.message || "알 수 없는 오류"}`, tone: "error" };
  } finally {
    await refreshLiveAgentProcessHistory();
    state.liveAgentProcessRowActionRunning = "";
    renderLobby({ followLatest: false });
  }
}

async function retryLiveAgentSessionRunNow(runId) {
  if (!runId || liveAgentProcessActionBusy()) return;
  state.liveAgentSessionRunRetryNowRunning = runId;
  state.liveAgentProcessStatus = { message: `${runId} 재시도 요청 중`, tone: "info" };
  renderLobby({ followLatest: false });
  try {
    const requestBody = {};
    if (document.querySelector("#live-agent-auto-join-real-provider-approval")?.checked === true) {
      requestBody.approve_real_providers = true;
    }
    const payload = await fetchJson(`/api/live-agent-session-runs/${encodeURIComponent(runId)}/retry-now`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(requestBody),
    });
    const run = payload?.session_run && typeof payload.session_run === "object" ? payload.session_run : {};
    const responseStatus = String(payload?.status || "scheduled");
    const label = responseStatus === "reconciled" ? "실행됨" : "예약됨";
    state.liveAgentProcessStatus = {
      message: `${run.run_id || runId} 재시도 ${label}`,
      tone: run.status === "ready" ? "success" : "info",
    };
  } catch (error) {
    state.liveAgentProcessStatus = { message: `${runId} 재시도 실패: ${error?.message || "알 수 없는 오류"}`, tone: "error" };
  } finally {
    await refreshLiveAgentProcessHistory();
    state.liveAgentSessionRunRetryNowRunning = "";
    renderLobby({ followLatest: false });
  }
}

async function pauseLiveAgentSessionRun(runId) {
  if (!runId || liveAgentProcessActionBusy()) return;
  state.liveAgentSessionRunActionRunning = runId;
  state.liveAgentProcessStatus = { message: `${runId} 일시정지 중`, tone: "info" };
  renderLobby({ followLatest: false });
  try {
    const payload = await fetchJson(`/api/live-agent-session-runs/${encodeURIComponent(runId)}/pause`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    const run = payload?.session_run && typeof payload.session_run === "object" ? payload.session_run : {};
    state.liveAgentProcessStatus = { message: `${run.run_id || runId} 일시정지됨`, tone: "success" };
  } catch (error) {
    state.liveAgentProcessStatus = { message: `${runId} 일시정지 실패: ${error?.message || "알 수 없는 오류"}`, tone: "error" };
  } finally {
    await refreshLiveAgentProcessHistory();
    state.liveAgentSessionRunActionRunning = "";
    renderLobby({ followLatest: false });
  }
}

async function resumeLiveAgentSessionRun(runId) {
  if (!runId || liveAgentProcessActionBusy()) return;
  state.liveAgentSessionRunActionRunning = runId;
  state.liveAgentProcessStatus = { message: `${runId} 재개 중`, tone: "info" };
  renderLobby({ followLatest: false });
  try {
    const payload = await fetchJson(`/api/live-agent-session-runs/${encodeURIComponent(runId)}/resume`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    const run = payload?.session_run && typeof payload.session_run === "object" ? payload.session_run : {};
    state.liveAgentProcessStatus = { message: `${run.run_id || runId} 재개됨`, tone: "success" };
  } catch (error) {
    state.liveAgentProcessStatus = { message: `${runId} 재개 실패: ${error?.message || "알 수 없는 오류"}`, tone: "error" };
  } finally {
    await refreshLiveAgentProcessHistory();
    state.liveAgentSessionRunActionRunning = "";
    renderLobby({ followLatest: false });
  }
}

async function stopLiveAgentSessionRun(runId) {
  if (!runId || liveAgentProcessActionBusy()) return;
  state.liveAgentSessionRunActionRunning = runId;
  state.liveAgentProcessStatus = { message: `${runId} 중지 중`, tone: "info" };
  renderLobby({ followLatest: false });
  try {
    const payload = await fetchJson(`/api/live-agent-session-runs/${encodeURIComponent(runId)}/stop`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    const run = payload?.session_run && typeof payload.session_run === "object" ? payload.session_run : {};
    state.liveAgentProcessStatus = { message: `${run.run_id || runId} 중지됨`, tone: "success" };
  } catch (error) {
    state.liveAgentProcessStatus = { message: `${runId} 중지 실패: ${error?.message || "알 수 없는 오류"}`, tone: "error" };
  } finally {
    await refreshLiveAgentProcessHistory();
    state.liveAgentSessionRunActionRunning = "";
    renderLobby({ followLatest: false });
  }
}

async function sendLiveAgentRegistration(form) {
  const agentId = form.querySelector("#live-agent-id")?.value.trim() || "";
  if (!agentId) return;
  const displayName = form.querySelector("#live-agent-display-name")?.value.trim() || agentId;
  const providerKind = form.querySelector("#live-agent-provider-kind")?.value || "manual";
  const connectionKind = form.querySelector("#live-agent-connection-kind")?.value || "manual";
  state.liveAgentStatus = { message: "에이전트 접속 등록 중", tone: "info" };
  renderLobby({ followLatest: false });
  try {
    const payload = await fetchJson("/api/live-agents", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        agent_id: agentId,
        display_name: displayName,
        provider_kind: providerKind,
        connection_kind: connectionKind,
        meeting_id: state.payload?.meeting?.meeting_id,
        engagement_mode: "mentioned",
        capabilities: ["room_chat", "mentions"],
      }),
    });
    setLiveAgents(payload.agents || []);
    state.liveAgentsLoaded = true;
    state.liveAgentStatus = { message: `${displayName} 접속 등록됨`, tone: "success" };
  } catch {
    state.liveAgentStatus = { message: "에이전트 접속 등록 실패", tone: "error" };
  }
  renderLobby({ followLatest: false });
}

async function generateLiveAgentJoinBrief(lobby) {
  const form = lobby.querySelector("#live-agent-form");
  if (!form || state.liveAgentJoinBriefRunning) return;
  const agentId = form.querySelector("#live-agent-id")?.value.trim() || "";
  if (!agentId) return;
  const displayName = form.querySelector("#live-agent-display-name")?.value.trim() || agentId;
  const providerKind = form.querySelector("#live-agent-provider-kind")?.value || "manual";
  const connectionKind = form.querySelector("#live-agent-connection-kind")?.value || "manual";
  state.liveAgentJoinBriefRunning = true;
  state.liveAgentJoinBrief = null;
  state.liveAgentStatus = { message: "에이전트 초대 패킷 생성 중", tone: "info" };
  renderLobby({ followLatest: false });
  try {
    const payload = await fetchJson("/api/live-agent-join-brief", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        agent_id: agentId,
        display_name: displayName,
        provider_kind: providerKind,
        connection_kind: connectionKind,
        meeting_id: state.payload?.meeting?.meeting_id || "",
        engagement_mode: "mentioned",
        timeout: 30,
        poll_interval: 2,
        max_chain_depth: 1,
      }),
    });
    state.liveAgentJoinBrief = payload;
    state.liveAgentStatus = { message: `${agentId} 초대 패킷 생성됨`, tone: "success" };
  } catch (error) {
    state.liveAgentJoinBrief = null;
    state.liveAgentStatus = { message: `에이전트 초대 패킷 실패: ${error?.message || "알 수 없는 오류"}`, tone: "error" };
  } finally {
    state.liveAgentJoinBriefRunning = false;
    renderLobby({ followLatest: false });
  }
}

async function updateLiveAgentEngagement(agentId, engagementMode) {
  if (!agentId || !engagementMode) return;
  state.liveAgentStatus = { message: `${agentId} 반응 정책 변경 중`, tone: "info" };
  renderLobby({ followLatest: false });
  try {
    const payload = await fetchJson(`/api/live-agents/${encodeURIComponent(agentId)}/engagement`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        engagement_mode: engagementMode,
      }),
    });
    setLiveAgents(payload.agents || []);
    state.liveAgentsLoaded = true;
    state.liveAgentStatus = { message: `${agentId} ${engagementMode} 모드`, tone: "success" };
  } catch (error) {
    state.liveAgentStatus = { message: `${agentId} 반응 정책 변경 실패: ${error?.message || "알 수 없는 오류"}`, tone: "error" };
  }
  await loadLiveAgentOperations({ background: true, force: true });
  renderLobby({ followLatest: false });
}

async function runLiveAgentProbe(agentId) {
  if (!agentId || state.liveAgentProbeRunning) return;
  state.liveAgentProbeRunning = agentId;
  state.liveAgentStatus = { message: `${agentId} probe 진행 중`, tone: "info" };
  renderLobby({ followLatest: false });
  try {
    const payload = await fetchJson(`/api/live-agents/${encodeURIComponent(agentId)}/probe`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ timeout_seconds: 12 }),
    });
    try {
      const lobbyPayload = await fetchJson("/api/lobby");
      setLobbyEvents(lobbyPayload.events || []);
    } catch {
      // Probe result is still useful if only the lobby refresh fails.
    }
    await refreshLiveAgentRuntimeSurfaces();
    const tone = payload.status === "ok" ? "success" : "error";
    state.liveAgentStatus = { message: `${agentId} probe ${payload.status || "unknown"}`, tone };
  } catch {
    state.liveAgentStatus = { message: `${agentId} probe 실패`, tone: "error" };
  } finally {
    state.liveAgentProbeRunning = "";
    renderLobby({ followLatest: false });
  }
}

async function loadCodexSessions(options = {}) {
  if (state.codexSessionsLoading && !options.force) return;
  const previousSignature = JSON.stringify(state.codexSessions || []);
  let shouldRender = !options.background;
  state.codexSessionsLoading = true;
  if (options.force) state.codexInviteStatus = { message: "Codex 세션 목록 갱신 중", tone: "info" };
  if (!options.background) renderLobby({ followLatest: false });
  try {
    const payload = await fetchJson("/api/codex-sessions?limit=20");
    state.codexSessions = payload.sessions || [];
    state.codexSessionsLoaded = true;
    shouldRender = shouldRender || JSON.stringify(state.codexSessions) !== previousSignature;
    if (!state.codexSessions.length) {
      state.codexInviteStatus = { message: "최근 Codex 세션 없음", tone: "info" };
      shouldRender = true;
    } else if (state.codexInviteStatus?.message === "Codex 세션 목록 갱신 중") {
      state.codexInviteStatus = null;
      shouldRender = true;
    }
  } catch {
    state.codexInviteStatus = { message: "Codex 세션 목록을 불러오지 못했습니다.", tone: "error" };
    state.codexSessionsLoaded = true;
    shouldRender = true;
  } finally {
    state.codexSessionsLoading = false;
    if (shouldRender) renderLobby({ followLatest: false });
  }
}

async function sendCodexSessionInvite(form) {
  const sessionId = form.querySelector("#codex-session-select")?.value || "";
  const roleId = form.querySelector("#codex-role-select")?.value || "";
  if (!sessionId || !roleId) return;
  state.codexInviteStatus = { message: "Codex 세션 초대 설정 생성 중", tone: "info" };
  renderLobby({ followLatest: false });
  try {
    const payload = await fetchJson("/api/codex-sessions/invite", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        meeting_id: state.payload?.meeting?.meeting_id,
        role_id: roleId,
        session_id: sessionId,
      }),
    });
    const role = (state.payload?.meeting?.roles || []).find((candidate) => candidate.id === roleId);
    const roleLabel = role?.display_name || roleId;
    const binding = payload.binding || {};
    state.codexInviteStatus = {
      message: `${roleLabel} · ${shortSessionId(binding.session_id || sessionId)} 연결됨`,
      tone: "success",
    };
    await loadLiveAgentOperations({ background: true, force: true });
  } catch {
    state.codexInviteStatus = { message: "Codex 세션 초대 실패", tone: "error" };
  }
  renderLobby({ followLatest: false });
}

async function sendCodexSessionJoin(form) {
  const sessionId = form?.querySelector("#codex-session-select")?.value || "";
  const roleId = form?.querySelector("#codex-role-select")?.value || "";
  if (!sessionId || !roleId) return;
  state.codexInviteStatus = { message: "Codex 세션 입장 중", tone: "info" };
  renderLobby({ followLatest: false });
  try {
    const payload = await fetchJson("/api/codex-sessions/join", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        meeting_id: state.payload?.meeting?.meeting_id,
        role_id: roleId,
        session_id: sessionId,
      }),
    });
    const role = (state.payload?.meeting?.roles || []).find((candidate) => candidate.id === roleId);
    const roleLabel = role?.display_name || roleId;
    const action = payload.action || "ensure";
    state.codexInviteStatus = {
      message: `${roleLabel} · ${shortSessionId(sessionId)} 입장됨 · ${action}`,
      tone: payload.status === "ready" ? "success" : "info",
    };
    notifyMeetingRefreshRequested(payload.meeting_id || state.payload?.meeting?.meeting_id);
    await Promise.all([
      loadLiveAgents({ background: true, force: true }),
      loadLiveAgentHealth({ background: true, force: true }),
      loadLiveAgentProcesses({ background: true, force: true }),
      loadLiveAgentOperations({ background: true, force: true }),
    ]);
  } catch {
    state.codexInviteStatus = { message: "Codex 세션 입장 실패", tone: "error" };
  }
  renderLobby({ followLatest: false });
}

async function sendLobbyAction(button) {
  const kind = button.dataset.lobbyAction || "message";
  let side = button.dataset.lobbySide || "mine";
  if (side === "mine" && button.dataset.lobbyName !== "나") side = "mine";
  if (side === "other" && button.dataset.lobbyName !== "상대") side = "other";
  const name = button.dataset.lobbyName || defaultLobbyName(side);
  const payload = await fetchJson("/api/lobby", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, side, kind, message: "" }),
  });
  setLobbyEvents(payload.events || []);
  renderLobby();
}

function defaultLobbyName(side) {
  if (side === "my-agent") return "내 에이전트";
  if (side === "other-agent") return "상대 에이전트";
  if (side === "other") return "상대";
  return "나";
}
