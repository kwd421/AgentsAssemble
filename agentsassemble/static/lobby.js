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
  });
  lobby.querySelector("#live-agent-process-refresh")?.addEventListener("click", () => {
    loadLiveAgentProcesses({ force: true });
    loadLiveAgentOperations({ force: true });
  });
  lobby.querySelector("#live-agent-process-smoke")?.addEventListener("click", async () => {
    await runLiveAgentSmoke(lobby);
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
  lobby.querySelectorAll("[data-live-agent-process-stop]").forEach((button) => {
    button.addEventListener("click", () => stopLiveAgentProcessGroup(button.dataset.liveAgentProcessStop));
  });
  lobby.querySelectorAll("[data-live-agent-process-restart]").forEach((button) => {
    button.addEventListener("click", () => restartLiveAgentProcessGroup(button.dataset.liveAgentProcessRestart));
  });
  lobby.querySelectorAll("[data-live-agent-engagement]").forEach((select) => {
    select.addEventListener("change", () => updateLiveAgentEngagement(select.dataset.liveAgentEngagement, select.value));
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
    autoRestart: Boolean(form.querySelector("#live-agent-process-auto-restart")?.checked),
    maxRestarts: form.querySelector("#live-agent-process-max-restarts")?.value ?? "",
    restartBackoff: form.querySelector("#live-agent-process-restart-backoff")?.value ?? "",
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
  const autoRestart = lobby.querySelector("#live-agent-process-auto-restart");
  const maxRestarts = lobby.querySelector("#live-agent-process-max-restarts");
  const restartBackoff = lobby.querySelector("#live-agent-process-restart-backoff");
  if (config) config.value = draft.configPath;
  if (group) group.value = draft.groupId;
  if (autoRestart) autoRestart.checked = draft.autoRestart;
  if (maxRestarts) maxRestarts.value = draft.maxRestarts;
  if (restartBackoff) restartBackoff.value = draft.restartBackoff;
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
  return `
    <section class="live-agent-processes" aria-label="상주 실행">
      <div class="roster-head">
        <strong>상주 실행</strong>
        <span>${counts.running} running · ${counts.restarting} restarting · ${counts.error} error · ${counts.total} groups</span>
      </div>
      ${renderProcessGroupHealthStrip(counts)}
      <form id="live-agent-process-form" class="live-agent-process-form">
        <input id="live-agent-process-config" maxlength="240" value="configs/live-agents.example.json" />
        <input id="live-agent-process-group" maxlength="64" placeholder="group id" />
        <label class="live-agent-process-options">
          <input id="live-agent-process-auto-restart" type="checkbox" />
          <span>auto restart</span>
        </label>
        <input id="live-agent-process-max-restarts" type="number" min="0" max="99" value="3" aria-label="max restarts" />
        <input id="live-agent-process-restart-backoff" type="number" min="0" max="3600" step="1" value="5" aria-label="restart backoff seconds" />
        <button type="submit" id="live-agent-process-start" ${processActionsDisabled ? "disabled" : ""}>시작</button>
        <button type="button" id="live-agent-preflight-check" ${processActionsDisabled ? "disabled" : ""}>예비점검</button>
        <button type="button" id="live-agent-process-smoke" ${processActionsDisabled ? "disabled" : ""}>진단</button>
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
  return state.liveAgentProcessStartRunning || state.liveAgentPreflightRunning || state.liveAgentSmokeRunning || state.liveAgentReadinessRunning;
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

function renderLiveAgentProcessCard(group) {
  const status = group.status || "unknown";
  const canStop = status === "running" || status === "restarting";
  const logTail = group.log_tail == null ? "" : String(group.log_tail);
  const agentLabel = liveAgentProcessAgentsLabel(group);
  const connectionLabel = liveAgentProcessConnectionLabel(group);
  const eventLabel = liveAgentProcessEventLabel(group);
  return `
    <article class="live-agent-process-row live-agent-process-${escapeHtml(status)}">
      <div>
        <strong>${escapeHtml(group.group_id || "live-agents")}</strong>
        <span>${escapeHtml(group.config_path || "")}</span>
        <small>${escapeHtml(group.pid ? `pid ${group.pid}` : "pid 없음")} · ${escapeHtml(group.server || "")}</small>
        <small>${escapeHtml(liveAgentProcessRestartLabel(group))}</small>
        ${agentLabel ? `<small class="live-agent-process-agents">${escapeHtml(agentLabel)}</small>` : ""}
        ${connectionLabel ? `<small class="live-agent-process-connection">${escapeHtml(connectionLabel)}</small>` : ""}
        ${eventLabel ? `<small class="live-agent-process-event">${escapeHtml(eventLabel)}</small>` : ""}
      </div>
      <em>${escapeHtml(liveAgentProcessStatusLabel(status))}</em>
      ${
        canStop
          ? `<button type="button" data-live-agent-process-stop="${escapeHtml(group.group_id || "")}">중지</button>`
          : `<button type="button" class="live-agent-process-restart" data-live-agent-process-restart="${escapeHtml(group.group_id || "")}">재시작</button>`
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
  const summary = String(operation.summary || operation.error || "").trim();
  return `
    <article class="live-agent-operation-row live-agent-operation-${escapeHtml(status)}">
      <div>
        <strong>${escapeHtml(operation.operation || "unknown")}</strong>
        <span>${escapeHtml(target)} · ${escapeHtml(operation.timestamp || "")}</span>
        ${summary ? `<small>${escapeHtml(summary)}</small>` : ""}
      </div>
      <em>${escapeHtml(status)}</em>
    </article>
  `;
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
  return timestamp ? `last event ${latest.event_type} · ${timestamp}` : `last event ${latest.event_type}`;
}

function renderLiveAgentCard(agent) {
  const status = agent.status || "offline";
  const runtimeDetails = liveAgentRuntimeDetails(agent);
  const lastError = String(agent.last_error || "").trim();
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
  return `auto restart ${count}/${max} · backoff ${backoff}s`;
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
    loadLiveAgents({ background: true }),
    loadLiveAgentProcesses({ background: true }),
    loadLiveAgentOperations({ background: true }),
  ]);
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

async function runLiveAgentReadiness(lobby) {
  if (liveAgentProcessActionBusy()) return;
  const groupId = lobby.querySelector("#live-agent-process-group")?.value.trim() || "";
  state.liveAgentReadinessRunning = true;
  state.liveAgentProcessStatus = { message: "상주 readiness 점검 중", tone: "info" };
  renderLobby({ followLatest: false });
  try {
    const payload = await fetchJson("/api/live-agent-readiness", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ group_id: groupId, timeout: 12 }),
    });
    try {
      const lobbyPayload = await fetchJson("/api/lobby");
      setLobbyEvents(lobbyPayload.events || []);
    } catch {
      // Readiness result is still useful if only the post-check lobby refresh fails.
    }
    await refreshLiveAgentRuntimeSurfaces();
    const tone = payload.status === "ready" ? "success" : "error";
    state.liveAgentProcessStatus = { message: `readiness ${payload.status || "unknown"}`, tone };
  } catch {
    state.liveAgentProcessStatus = { message: "readiness 점검 실패", tone: "error" };
  } finally {
    state.liveAgentReadinessRunning = false;
    renderLobby({ followLatest: false });
  }
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

async function stopLiveAgentProcessGroup(groupId) {
  if (!groupId) return;
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
  }
  await loadLiveAgentOperations({ background: true, force: true });
  renderLobby({ followLatest: false });
}

async function restartLiveAgentProcessGroup(groupId) {
  if (!groupId) return;
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
  }
  await loadLiveAgentOperations({ background: true, force: true });
  renderLobby({ followLatest: false });
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
