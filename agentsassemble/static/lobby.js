import { displayTopic, escapeHtml, fetchJson, state } from "./shared.js";

const lobbySides = new Set(["mine", "my-agent", "other", "other-agent"]);

export function renderLobby() {
  const lobby = document.querySelector("#lobby");
  if (!lobby) return;
  const roster = buildLobbyRoster(state.lobbyEvents);
  const shouldFollowLatest = isLobbyFeedNearBottom(lobby);
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
            <button type="submit">보내기</button>
          </form>
        </div>
        ${renderLobbyRoster(roster)}
      </div>
    </section>
  `;
  const form = lobby.querySelector("#lobby-form");
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    await sendLobbyEvent("message");
  });
  const myNameInput = lobby.querySelector("#lobby-my-name");
  myNameInput?.addEventListener("input", () => {
    localStorage.setItem("agentsassemble.name", myNameInput.value.trim());
  });
  lobby.querySelectorAll("[data-lobby-action]").forEach((button) => {
    button.addEventListener("click", () => sendLobbyAction(button));
  });
  if (shouldFollowLatest) scrollLobbyFeedToLatest(lobby);
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
    </aside>
  `;
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

function renderLobbySideOptions() {
  const current = localStorage.getItem("agentsassemble.lobbySide") || "mine";
  return [
    ["mine", "나"],
    ["my-agent", "내 에이전트"],
    ["other", "상대"],
    ["other-agent", "상대 에이전트"],
  ]
    .map(([value, label]) => `<option value="${value}" ${value === current ? "selected" : ""}>${label}</option>`)
    .join("");
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

async function sendLobbyEvent(kind) {
  const messageInput = document.querySelector("#lobby-message");
  const side = "mine";
  const name = localStorage.getItem("agentsassemble.name") || defaultLobbyName(side);
  const message = messageInput?.value.trim() || "";
  if (kind === "message" && !message) return;
  const payload = await fetchJson("/api/lobby", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, side, kind, message }),
  });
  state.lobbyEvents = payload.events || [];
  if (messageInput) messageInput.value = "";
  renderLobby();
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
  state.lobbyEvents = payload.events || [];
  renderLobby();
}

function defaultLobbyName(side) {
  if (side === "my-agent") return "내 에이전트";
  if (side === "other-agent") return "상대 에이전트";
  if (side === "other") return "상대";
  return "나";
}
