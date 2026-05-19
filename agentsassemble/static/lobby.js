import {
  bindingSummary,
  displayTopic,
  escapeHtml,
  fetchJson,
  roleMeta,
  setLiveAgentOperations,
  setLiveAgentProcesses,
  setLiveAgents,
  setLobbyEvents,
  state,
} from "./shared.js";

const lobbySides = new Set(["mine", "my-agent", "other", "other-agent"]);

export function renderLobby(options = {}) {
  const lobby = document.querySelector("#lobby");
  if (!lobby) return;
  const focusedId = document.activeElement?.id;
  const focusedSelection = readFocusedSelection(document.activeElement);
  const draftMessage = lobby.querySelector("#lobby-message")?.value || "";
  const processDraft = readLiveAgentProcessDraft(lobby);
  const registrationDraft = readLiveAgentRegistrationDraft(lobby);
  const previousFeed = lobby.querySelector(".lobby-feed");
  const previousScrollTop = previousFeed?.scrollTop || 0;
  const roster = buildLobbyRoster(state.lobbyEvents);
  const shouldFollowLatest = options.followLatest ?? isLobbyFeedNearBottom(lobby);
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
            <input id="lobby-message" maxlength="240" placeholder="메시지를 입력하세요" />
            ${hasRemoteLobbyBridge() ? '<button type="button" id="lobby-ask-remote">원격 호출</button>' : ""}
            <button type="submit">보내기</button>
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
    loadLiveAgentOperations({ force: true });
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
  lobby.querySelector("#live-agent-call-round")?.addEventListener("click", async () => {
    await callLiveAgentOfficialRound(lobby);
  });
  lobby.querySelector("#live-agent-call-remaining-rounds")?.addEventListener("click", async () => {
    await callLiveAgentRemainingRounds(lobby);
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
  lobby.querySelector("#codex-session-refresh")?.addEventListener("click", () => {
    loadCodexSessions({ force: true });
  });
  lobby.querySelector("#codex-invite-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    await sendCodexSessionInvite(event.currentTarget);
  });
  if (!state.codexSessionsLoaded && !state.codexSessionsLoading) {
    loadCodexSessions();
  }
  if (!state.liveAgentsLoaded && !state.liveAgentsLoading) {
    loadLiveAgents();
  }
  if (!state.liveAgentHealthLoaded && !state.liveAgentHealthLoading) {
    loadLiveAgentHealth();
  }
  if (!state.liveAgentProcessesLoaded && !state.liveAgentProcessesLoading) {
    loadLiveAgentProcesses();
  }
  if (!state.liveAgentOperationsLoaded && !state.liveAgentOperationsLoading) {
    loadLiveAgentOperations();
  }
  if (shouldFollowLatest) scrollLobbyFeedToLatest(lobby);
  else restoreLobbyFeedScroll(lobby, previousScrollTop);
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
    sessionRunRemainingRounds: Boolean(form.querySelector("#live-agent-session-run-remaining-rounds")?.checked),
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
  const sessionRunRemainingRounds = lobby.querySelector("#live-agent-session-run-remaining-rounds");
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
  if (sessionRunRemainingRounds) sessionRunRemainingRounds.checked = draft.sessionRunRemainingRounds;
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

function scrollLobbyFeedToLatest(lobby) {
  const feed = lobby.querySelector(".lobby-feed");
  if (!feed) return;
  requestAnimationFrame(() => {
    feed.scrollTop = feed.scrollHeight;
  });
}

function restoreLobbyFeedScroll(lobby, scrollTop) {
  const feed = lobby.querySelector(".lobby-feed");
  if (!feed) return;
  requestAnimationFrame(() => {
    feed.scrollTop = scrollTop;
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

function renderLobbyEvent(event) {
  const currentName = localStorage.getItem("agentsassemble.name") || "";
  const storedSide = lobbySides.has(event.side) ? event.side : "";
  const side = storedSide || (currentName && event.name === currentName ? "mine" : "other");
  const content = event.message || defaultLobbyMessage(event.kind, side);
  const name = event.name || "guest";
  const sideLabel = lobbySideLabel(side);
  const showSideLabel = name !== sideLabel;
  return `
    <article class="lobby-event lobby-${escapeHtml(event.kind || "message")} lobby-${side}">
      <div class="lobby-avatar">${escapeHtml(initials(name))}</div>
      <div class="lobby-bubble">
        <div class="lobby-meta">
          <strong>${escapeHtml(name)}</strong>
          ${showSideLabel ? `<span>${escapeHtml(sideLabel)}</span>` : ""}
          <span>${escapeHtml(lobbyKindLabel(event.kind))}</span>
        </div>
        <p>${escapeHtml(content)}</p>
      </div>
    </article>
  `;
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
          <option value="remote_bridge">Remote bridge</option>
          <option value="codex_resume">Codex resume</option>
          <option value="manual">Manual</option>
        </select>
        <button type="submit" ${state.liveAgentsLoading ? "disabled" : ""}>접속 등록</button>
        <button type="button" id="live-agent-refresh">갱신</button>
      </form>
      ${status ? `<p class="live-agent-status" data-tone="${escapeHtml(status.tone || "info")}">${escapeHtml(status.message)}</p>` : ""}
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
  return `
    <section class="live-agent-processes" aria-label="상주 실행">
      <div class="roster-head">
        <strong>상주 실행</strong>
        <span>${counts.running} running · ${counts.restarting} restarting · ${counts.error} error · ${counts.total} groups</span>
      </div>
      ${renderLiveAgentRuntimeHealth(state.liveAgentHealth, state.liveAgentHealthLoading)}
      ${renderProcessGroupHealthStrip(counts)}
      <form id="live-agent-process-form" class="live-agent-process-form">
        <input id="live-agent-process-config" maxlength="240" value="configs/live-agents.start-session.example.json" />
        <input id="live-agent-process-group" maxlength="64" placeholder="group id" />
        <input id="live-agent-session-meeting-id" maxlength="128" placeholder="meeting id" value="${escapeHtml(defaultMeetingId)}" data-default-value="${escapeHtml(defaultMeetingId)}" />
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
          <input id="live-agent-process-auto-restart" type="checkbox" />
          <span>auto restart</span>
        </label>
        <input id="live-agent-process-max-restarts" type="number" min="0" max="99" value="3" aria-label="max restarts" />
        <input id="live-agent-process-restart-backoff" type="number" min="0" max="3600" step="1" value="5" aria-label="restart backoff seconds" />
        <input id="live-agent-process-stale-restart-after" type="number" min="0" max="86400" step="1" value="0" aria-label="stale restart after seconds" />
        <button type="submit" id="live-agent-process-start" ${processActionsDisabled ? "disabled" : ""}>시작</button>
        <button type="button" id="live-agent-process-stop-running" ${processActionsDisabled ? "disabled" : ""}>실행중지</button>
        <button type="button" id="live-agent-session-start" ${processActionsDisabled ? "disabled" : ""}>세션시작</button>
        <button type="button" id="live-agent-session-resume" ${processActionsDisabled ? "disabled" : ""}>세션재개</button>
        <button type="button" id="live-agent-session-restart" ${processActionsDisabled ? "disabled" : ""}>세션재시작</button>
        <button type="button" id="live-agent-session-recover" ${processActionsDisabled ? "disabled" : ""}>세션복구</button>
        <button type="button" id="live-agent-session-check" ${processActionsDisabled ? "disabled" : ""}>세션점검</button>
        <button type="button" id="live-agent-session-stop" ${processActionsDisabled ? "disabled" : ""}>세션중지</button>
        <button type="button" id="live-agent-call-round" ${processActionsDisabled ? "disabled" : ""}>라운드호출</button>
        <button type="button" id="live-agent-call-remaining-rounds" ${processActionsDisabled ? "disabled" : ""}>남은라운드</button>
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
      </form>
      <div class="live-agent-process-list">
        ${
          groups.length
            ? groups.map(renderLiveAgentProcessCard).join("")
            : '<p class="roster-empty">실행 중인 상주 그룹이 없습니다.</p>'
        }
      </div>
      ${renderLiveAgentOperations()}
      ${status ? `<p class="live-agent-status" data-tone="${escapeHtml(status.tone || "info")}">${escapeHtml(status.message)}</p>` : ""}
    </section>
  `;
}

function liveAgentProcessActionBusy() {
  return state.liveAgentProcessStartRunning || state.liveAgentSessionStartRunning || state.liveAgentSessionRestartRunning || state.liveAgentSessionRecoverRunning || state.liveAgentSessionCheckRunning || state.liveAgentSessionStopRunning || state.liveAgentRoundCallRunning || state.liveAgentPreflightRunning || state.liveAgentSmokeRunning || state.liveAgentOfficialRoundSmokeRunning || state.liveAgentSessionSmokeRunning || state.liveAgentReadinessRunning || Boolean(state.liveAgentProcessRowActionRunning) || state.liveAgentProcessBulkStopRunning;
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
  const connections = health.connections && typeof health.connections === "object" ? health.connections : {};
  const sessions = health.sessions && typeof health.sessions === "object" ? health.sessions : {};
  const processCounts = processes.counts && typeof processes.counts === "object" ? processes.counts : {};
  const agentLive = Math.max(0, Number(agents.live || 0));
  const agentTotal = Math.max(0, Number(agents.total || 0));
  const runningProcesses = Math.max(0, Number(processCounts.running || 0));
  const processTotal = Math.max(0, Number(processes.total || 0));
  const connected = Math.max(0, Number(connections.connected || 0));
  const expected = Math.max(0, Number(connections.expected || 0));
  const readySessions = Math.max(0, Number(sessions.ready || 0));
  const sessionTotal = Math.max(0, Number(sessions.total || 0));
  const attentionCount = liveAgentHealthAttentionCount(health);
  const sessionAttention = liveAgentHealthAttentionSummary(sessions.attention, "session attention");
  const tone = status === "ok" ? "success" : status === "degraded" ? "warning" : "error";
  return (
    `<p class="live-agent-runtime-health" data-tone="${escapeHtml(tone)}">` +
    `runtime health ${escapeHtml(status)} · ` +
    `agents ${escapeHtml(`${agentLive}/${agentTotal}`)} live · ` +
    `processes ${escapeHtml(`${runningProcesses}/${processTotal}`)} running · ` +
    `connections ${escapeHtml(`${connected}/${expected}`)} connected · ` +
    `sessions ${escapeHtml(`${readySessions}/${sessionTotal}`)} ready · ` +
    `attention ${escapeHtml(attentionCount)}` +
    (sessionAttention ? `<br><small>${escapeHtml(sessionAttention)}</small>` : "") +
    "</p>"
  );
}

function liveAgentHealthAttentionCount(health) {
  const sections = [health?.agents, health?.processes, health?.connections, health?.sessions];
  return sections.reduce((count, section) => {
    const attention = section && typeof section === "object" && Array.isArray(section.attention) ? section.attention : [];
    return count + attention.length;
  }, 0);
}

function liveAgentHealthAttentionSummary(value, label) {
  if (!Array.isArray(value) || value.length === 0) return "";
  const cleaned = value.map((item) => String(item || "").trim()).filter(Boolean).slice(0, 3);
  if (!cleaned.length) return "";
  const remaining = Math.max(0, value.length - cleaned.length);
  const suffix = remaining > 0 ? ` +${remaining} more` : "";
  return `${label} ${cleaned.join(", ")}${suffix}`;
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
    .slice(0, 7)
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
      "session_smoke_reply_count",
      "session_smoke_post_restart_reply_count",
      "session_smoke_post_recover_reply_count",
      "session_smoke_soak_cycle_count",
      "session_smoke_soak_reply_count",
      "session_smoke_soak_check_statuses",
      "probe_statuses",
    ];
  }
  return [];
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
      ${runtimeDetails ? `<small class="live-agent-runtime">${escapeHtml(runtimeDetails)}</small>` : ""}
      ${lastError ? `<small class="live-agent-error-detail">${escapeHtml(lastError)}</small>` : ""}
    </article>
  `;
}

function renderEngagementModeOptions(currentMode) {
  const current = String(currentMode || "mentioned");
  return [
    ["always", "always (loop-prone)"],
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
  if (agent.last_observed_event_id) details.push(`cursor ${shortSessionId(agent.last_observed_event_id)}`);
  if (agent.last_observed_live_event_id) details.push(`official cursor ${shortSessionId(agent.last_observed_live_event_id)}`);
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
  return kind || "Manual";
}

function connectionKindLabel(kind) {
  if (kind === "local_cli") return "Local CLI";
  if (kind === "live_session") return "Live session";
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

async function sendLobbyEvent(kind, options = {}) {
  const lobby = document.querySelector("#lobby");
  const messageInput = document.querySelector("#lobby-message");
  const side = "mine";
  const name = localStorage.getItem("agentsassemble.name") || defaultLobbyName(side);
  const previousValue = messageInput?.value || "";
  const message = previousValue.trim();
  if (kind === "message" && !message) return;
  const shouldFollowLatest = isLobbyFeedNearBottom(lobby);
  if (messageInput && kind === "message") messageInput.value = "";
  let payload;
  try {
    payload = await fetchJson("/api/lobby", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, side, kind, message }),
    });
  } catch (error) {
    const activeInput = document.querySelector("#lobby-message");
    if (activeInput && kind === "message" && activeInput.value === "") {
      activeInput.value = previousValue;
      activeInput.focus();
    }
    throw error;
  }
  setLobbyEvents(payload.events || []);
  renderLobby({ followLatest: shouldFollowLatest });
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
  renderLobby({ followLatest: options.followLatest ?? isLobbyFeedNearBottom(document.querySelector("#lobby")) });
  document.querySelector("#lobby-message")?.focus();
}

async function loadLiveAgents(options = {}) {
  if (state.liveAgentsLoading && !options.force) return;
  const previousSignature = JSON.stringify(state.liveAgents || []);
  let shouldRender = !options.background;
  state.liveAgentsLoading = true;
  if (options.force) state.liveAgentStatus = { message: "살아있는 에이전트 갱신 중", tone: "info" };
  if (!options.background) renderLobby({ followLatest: false });
  try {
    const payload = await fetchJson("/api/live-agents");
    const agents = payload.agents || [];
    setLiveAgents(agents);
    state.liveAgentsLoaded = true;
    shouldRender = shouldRender || JSON.stringify(agents) !== previousSignature;
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
    loadLiveAgentProcesses({ background: true }),
    loadLiveAgentOperations({ background: true }),
  ]);
}

async function loadLiveAgentHealth(options = {}) {
  if (state.liveAgentHealthLoading && !options.force) return;
  const previousSignature = JSON.stringify(state.liveAgentHealth || null);
  let shouldRender = !options.background;
  state.liveAgentHealthLoading = true;
  if (!options.background) renderLobby({ followLatest: false });
  try {
    const payload = await fetchJson("/api/live-agent-health");
    state.liveAgentHealth = payload;
    state.liveAgentHealthLoaded = true;
    shouldRender = shouldRender || JSON.stringify(payload) !== previousSignature;
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
    await loadLiveAgentOperations({ background: true, force: true });
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
    const payload = await fetchJson("/api/live-agent-sessions/restart", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        meeting_id: meetingId,
        group_id: groupId,
        connect_timeout_seconds: liveAgentSessionConnectTimeoutSeconds(lobby),
      }),
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
    const payload = await fetchJson("/api/live-agent-sessions/recover", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        meeting_id: meetingId,
        group_id: groupId,
        connect_timeout_seconds: liveAgentSessionConnectTimeoutSeconds(lobby),
      }),
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
    await loadLiveAgentOperations({ background: true, force: true });
    state.liveAgentProcessBulkStopRunning = false;
    renderLobby({ followLatest: false });
  }
}

async function runLiveAgentSessionAction(lobby, { endpoint, includeCouncilConfigs, busyMessage, failurePrefix, notifyRecoverable }) {
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

function liveAgentSessionConnectTimeoutSeconds(lobby) {
  const value = Number(lobby.querySelector("#live-agent-session-connect-timeout")?.value || 5);
  if (!Number.isFinite(value)) return 5;
  return Math.min(120, Math.max(0, value));
}

function liveAgentSessionStatusMessage(payload) {
  const status = payload.status || "unknown";
  const meetingId = payload.meeting_id || "meeting";
  const connection = payload.connection && typeof payload.connection === "object" ? payload.connection : {};
  const expected = Math.max(0, Number(connection.expected || 0));
  const connected = Math.max(0, Number(connection.connected || 0));
  const autoRounds = liveAgentSessionAutoRoundsLabel(payload);
  return `세션 ${status}: ${meetingId} · ${connected}/${expected} connected${autoRounds ? ` · ${autoRounds}` : ""}`;
}

function liveAgentSessionStopStatusMessage(payload) {
  const offline = payload?.offline && typeof payload.offline === "object" ? payload.offline : {};
  return `세션 ${payload?.status || "unknown"}: ${payload?.meeting_id || "unknown"} · ${payload?.group_id || "unknown"} · ${offline.offline || 0}/${offline.expected || 0} offline`;
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
  const autoRounds = payload.auto_rounds && typeof payload.auto_rounds === "object" ? payload.auto_rounds : null;
  if (!autoRounds) return "success";
  return autoRounds.status === "answered" || autoRounds.status === "complete" ? "success" : "error";
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
    await loadLiveAgentOperations({ background: true, force: true });
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
    await loadLiveAgentOperations({ background: true, force: true });
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
    await loadLiveAgentOperations({ background: true, force: true });
    state.liveAgentProcessRowActionRunning = "";
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
  state.codexSessionsLoading = true;
  if (options.force) state.codexInviteStatus = { message: "Codex 세션 목록 갱신 중", tone: "info" };
  renderLobby({ followLatest: false });
  try {
    const payload = await fetchJson("/api/codex-sessions?limit=20");
    state.codexSessions = payload.sessions || [];
    state.codexSessionsLoaded = true;
    if (!state.codexSessions.length) {
      state.codexInviteStatus = { message: "최근 Codex 세션 없음", tone: "info" };
    } else if (state.codexInviteStatus?.message === "Codex 세션 목록 갱신 중") {
      state.codexInviteStatus = null;
    }
  } catch {
    state.codexInviteStatus = { message: "Codex 세션 목록을 불러오지 못했습니다.", tone: "error" };
    state.codexSessionsLoaded = true;
  } finally {
    state.codexSessionsLoading = false;
    renderLobby({ followLatest: false });
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
  } catch {
    state.codexInviteStatus = { message: "Codex 세션 초대 실패", tone: "error" };
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
