const state = {
  currentTab: "lobby",
  meetings: [],
  payload: null,
  archiveKey: "decision.md",
  lobbyEvents: [],
};

const roleClass = {
  lore_lawyer: "red",
  show_me_the_feats: "cyan",
  fanboard_skeptic: "green",
};

const roleMeta = {
  lore_lawyer: { color: "red", title: "공식 설정 담당", badge: "설정/정합성", avatar: "/static/avatar-lore.svg" },
  show_me_the_feats: { color: "cyan", title: "전투 묘사 담당", badge: "전적/퍼포먼스", avatar: "/static/avatar-feats.svg" },
  fanboard_skeptic: { color: "green", title: "게시판식 반례 검증 담당", badge: "갤럼/반박", avatar: "/static/avatar-skeptic.svg" },
};

const lensLabels = {
  "Canon Analyst": "공식 설정 분석",
  "Feats Analyst": "전투 묘사 분석",
  "Skeptical Critic": "반례 검증",
};

const focusLabels = {
  lore_lawyer: "공식 언급, 설정 우선순위, 원작 정합성을 봅니다.",
  show_me_the_feats: "실제 전투 장면, 승패, 능력 사용 결과를 봅니다.",
  fanboard_skeptic: "팬덤 과장, 약한 근거, 반례와 불확실성을 커뮤니티식으로 세게 찌릅니다.",
};

const roundLabels = {
  round_1: "1라운드 · 첫 주장",
  round_2: "2라운드 · 반박/비교",
};

function roundLabel(meeting, roundId, fallback) {
  const templateRound = (meeting.meeting_template?.rounds || []).find((round) => round.id === roundId);
  return roundLabels[roundId] || templateRound?.title || fallback || roundId;
}

function displayTopic(meeting) {
  if (meeting.display_topic) return meeting.display_topic;
  if (meeting.topic === "One Piece admiral strength debate") return "원피스 3대장 최강자 토론";
  return meeting.topic || "회의";
}

function displayQuestion(question) {
  const meeting = state.payload?.meeting;
  if (meeting?.display_question) return meeting.display_question;
  if (question === "Who is the strongest One Piece admiral?") return "원피스 3대장 중 누가 제일 센가?";
  return question || "";
}

const tabs = document.querySelectorAll(".tab");
const panels = document.querySelectorAll(".panel");
const emptyState = document.querySelector("#empty-state");
const runDemo = document.querySelector("#run-demo");
const meetingSelect = document.querySelector("#meeting-select");
const subtitle = document.querySelector("#meeting-subtitle");
const uiScale = document.querySelector("#ui-scale");
const textScale = document.querySelector("#text-scale");
const appStatus = document.querySelector("#app-status");
const lobbySides = new Set(["mine", "my-agent", "other", "other-agent"]);

function applyScaleSettings() {
  const ui = localStorage.getItem("agentsassemble.uiScale") || "90";
  const text = localStorage.getItem("agentsassemble.textScale") || "90";
  document.documentElement.style.setProperty("--ui-scale", String(Number(ui) / 100));
  document.documentElement.style.setProperty("--text-scale", String(Number(text) / 100));
  if (uiScale) uiScale.value = ui;
  if (textScale) textScale.value = text;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function showAppStatus(message, tone = "info") {
  if (!appStatus) return;
  appStatus.textContent = message;
  appStatus.dataset.tone = tone;
  appStatus.hidden = !message;
}

async function fetchJson(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) throw new Error(`Request failed: ${response.status}`);
  return response.json();
}

async function loadMeetings() {
  const data = await fetchJson("/api/meetings");
  state.meetings = data.meetings || [];
  meetingSelect.innerHTML = "";
  if (!state.meetings.length) {
    meetingSelect.innerHTML = '<option value="">회의 없음</option>';
    return null;
  }
  for (const meeting of state.meetings) {
    const option = document.createElement("option");
    option.value = meeting.meeting_id;
    option.textContent = `${meeting.meeting_id} · ${displayTopic(meeting)}`;
    meetingSelect.append(option);
  }
  return state.meetings[0].meeting_id;
}

async function loadMeeting(meetingId) {
  const url = meetingId ? `/api/meetings/${encodeURIComponent(meetingId)}` : "/api/meetings/latest";
  const payload = await fetchJson(url);
  state.payload = payload.meeting === null ? null : payload;
  render();
}

async function loadLobby() {
  const payload = await fetchJson("/api/lobby");
  state.lobbyEvents = payload.events || [];
  renderLobby();
}

function setActiveTab(tabId) {
  state.currentTab = tabId;
  tabs.forEach((tab) => {
    const isActive = tab.dataset.tab === tabId;
    tab.classList.toggle("is-active", isActive);
    tab.setAttribute("aria-selected", String(isActive));
    tab.tabIndex = isActive ? 0 : -1;
  });
  panels.forEach((panel) => {
    const isActive = panel.id === tabId;
    panel.classList.toggle("is-active", isActive);
    panel.hidden = !isActive;
  });
}

function render() {
  const payload = state.payload;
  const hasMeeting = payload && payload.meeting;
  renderLobby();
  if (!hasMeeting) {
    setActiveTab(state.currentTab);
    emptyState.classList.toggle("is-active", state.currentTab !== "lobby");
    return;
  }
  emptyState.classList.remove("is-active");

  setActiveTab(state.currentTab);
  subtitle.textContent = `${displayTopic(payload.meeting)} · ${payload.meeting.meeting_id}`;
  renderLive(payload);
  renderBoard(payload);
  renderArchive(payload);
}

function renderLobby() {
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
          <div class="lobby-stage">
            ${renderAssembleRing(roster)}
          </div>
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

function renderAssembleRing(roster) {
  const allMembers = buildAssembleMembers(roster);
  const members = allMembers.slice(0, 12);
  const hiddenCount = Math.max(0, allMembers.length - members.length);
  const countLabel = hiddenCount ? `${members.length}+${hiddenCount}` : String(members.length);
  return `
    <section class="assemble-ring" aria-label="집결 현황">
      <div class="assemble-core">
        <span>ASSEMBLE</span>
        <strong>${escapeHtml(countLabel)}</strong>
        <small>집결 중</small>
      </div>
      ${members.map((member, index) => renderAssembleMember(member, index, members.length)).join("")}
    </section>
  `;
}

function buildAssembleMembers(roster) {
  return roster.flatMap((user) => {
    const owner = {
      kind: user.key === "mine" ? "mine" : "other",
      label: user.name,
      title: user.key === "mine" ? "나" : "상대",
    };
    const agents = user.agents.map((agent) => ({
      kind: user.key === "mine" ? "my-agent" : "other-agent",
      label: agent.name,
      title: agent.deploy ? "투입" : agent.ready ? "준비" : "대기",
    }));
    return [owner, ...agents];
  });
}

function renderAssembleMember(member, index, total) {
  const angle = total <= 1 ? -90 : -90 + (360 / total) * index;
  return `
    <div class="assemble-member assemble-${escapeHtml(member.kind)}" style="--angle:${angle}deg">
      <span>${escapeHtml(initials(member.label))}</span>
      <small>${escapeHtml(member.title)}</small>
    </div>
  `;
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

function renderLive(payload) {
  const roles = payload.meeting.roles || [];
  const rounds = payload.meeting.debate_rounds || [];
  const live = document.querySelector("#live");
  const shouldFollowLatest = isLiveTranscriptNearBottom(live);
  const messages = rounds.flatMap((round) =>
    (round.messages || []).map((message) => ({ ...message, roundTitle: roundLabel(payload.meeting, round.id, round.title) }))
  );
  const synthesis = payload.meeting.moderator_synthesis || {};
  live.innerHTML = `
    <div class="live-room">
      <section class="live-hero">
        <div class="live-hero-copy">
          <div class="live-statusbar">
            <span class="live-pill">공식 실황</span>
            <strong>Round ${escapeHtml(rounds.length || 0)}</strong>
            <span>합의도 ${escapeHtml(synthesis.confidence || "unknown")}</span>
          </div>
          <div class="live-hero-title">
            <h2>${escapeHtml(displayQuestion(payload.meeting.question))}</h2>
            <div class="channel-tabs" aria-label="발언 대상">
              <span class="is-active">전체</span>
              <span>팀</span>
              <span>귓속말</span>
            </div>
          </div>
        </div>
        ${renderLiveCouncilRing(roles)}
      </section>
      <section class="live-bottom">
        ${renderLiveTimeline(payload, messages)}
        <main class="message-list live-transcript" aria-label="공식 토론 기록" aria-live="polite">
          <div class="feed-head">
            <div>
              <strong>토론 feed</strong>
              <span>독립 리서치 완료 · Round 1/2 진행 기록</span>
            </div>
            <em class="record-badge">공식 기록</em>
          </div>
          ${messages.map(renderMessage).join("")}
          <article class="message message-purple message-moderator">
            <img class="profile" src="/static/avatar-moderator.svg" alt="" />
            <div class="message-body">
            <div class="message-header"><span class="speaker"><strong>Moderator</strong><em>종합</em></span><span class="message-route">전체 · <span class="confidence">${escapeHtml(synthesis.confidence || "")}</span></span></div>
            <p>${escapeHtml(synthesis.summary || "")}</p>
            </div>
          </article>
        </main>
        ${renderLiveOutcome(payload, messages)}
      </section>
    </div>
  `;
  if (shouldFollowLatest) scrollLiveTranscriptToLatest(live);
}

function isLiveTranscriptNearBottom(live) {
  const feed = live?.querySelector(".live-transcript");
  if (!feed) return true;
  const distanceFromBottom = feed.scrollHeight - feed.scrollTop - feed.clientHeight;
  return distanceFromBottom < 64;
}

function scrollLiveTranscriptToLatest(live) {
  const feed = live?.querySelector(".live-transcript");
  if (!feed) return;
  requestAnimationFrame(() => {
    feed.scrollTop = feed.scrollHeight;
  });
}

function renderLiveTimeline(payload, messages) {
  const rounds = payload.meeting.debate_rounds || [];
  return `
    <aside class="live-timeline">
      <strong>진행</strong>
      <ol>
        <li class="is-done"><span></span>회의 시작</li>
        <li class="is-done"><span></span>독립 리서치</li>
        <li class="${rounds.length > 1 ? "is-current" : "is-done"}"><span></span>Round ${escapeHtml(rounds.length || 1)}</li>
        <li><span></span>결정 생성</li>
      </ol>
      ${renderRailMetric("발언 수", messages.length)}
      ${renderRailMetric("라운드", `${rounds.length || 0} / 3`)}
    </aside>
  `;
}

function renderLiveOutcome(payload, messages) {
  const synthesis = payload.meeting.moderator_synthesis || {};
  return `
    <aside class="live-outcome">
      <div class="outcome-card">
        <span>현재 판정</span>
        <strong>${escapeHtml(synthesis.winner || "판정 대기")}</strong>
        <p>${escapeHtml(synthesis.summary || "아직 종합 의견이 없습니다.")}</p>
      </div>
      <div class="consensus-card">
        <strong>합의도 추이</strong>
        <div class="consensus-score">${escapeHtml(synthesis.confidence || "unknown")}</div>
        <div class="consensus-track"><span></span></div>
        <p>${escapeHtml(messages.length)}개 발언 기반</p>
      </div>
      <section class="rail-card rail-compact">
        <strong>최근 산출물</strong>
        ${renderArtifactRow("결정안", "decision.md")}
        ${renderArtifactRow("발언 로그", "transcript.md")}
        ${renderArtifactRow("의제", "agenda.md")}
      </section>
    </aside>
  `;
}

function renderLiveStatusRail(payload, messages) {
  const synthesis = payload.meeting.moderator_synthesis || {};
  const roundCount = payload.meeting.debate_rounds?.length || 0;
  return `
    <aside class="command-rail">
      <section class="rail-card rail-live">
        <div class="rail-card-head">
          <strong>토론 진행 중</strong>
          <span>LIVE</span>
        </div>
        <small>Round ${escapeHtml(roundCount)} · ${escapeHtml(synthesis.confidence || "unknown")}</small>
        <p>${escapeHtml(displayQuestion(payload.meeting.question))}</p>
        <button type="button">토론 정보</button>
      </section>
      <section class="rail-card rail-compact">
        <strong>진행 상황</strong>
        ${renderRailMetric("라운드", `${roundCount} / 3`)}
        ${renderRailMetric("발언 수", messages.length)}
        ${renderRailMetric("합의도", synthesis.confidence || "unknown")}
      </section>
      <section class="rail-card rail-compact">
        <strong>최근 산출물</strong>
        ${renderArtifactRow("결정안", "decision.md")}
        ${renderArtifactRow("근거 요약", "evidence.md")}
        ${renderArtifactRow("발언 로그", "transcript.md")}
        ${renderArtifactRow("의제", "agenda.md")}
      </section>
    </aside>
  `;
}

function renderRailMetric(label, value) {
  return `<div class="rail-metric"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`;
}

function renderArtifactRow(label, filename) {
  return `<div class="artifact-row"><span>${escapeHtml(label)}</span><em>${escapeHtml(filename)}</em></div>`;
}

function renderLiveCouncilRing(roles) {
  const members = [
    { label: "나", title: "Owner", color: "mine", avatar: "" },
    ...roles.map((role) => {
      const meta = roleMeta[role.id] || { color: "purple", title: role.lens, avatar: "/static/avatar-moderator.svg" };
      return { label: role.display_name, title: meta.badge || meta.title, color: meta.color, avatar: meta.avatar };
    }),
    { label: "친구봇", title: "deploy", color: "purple", avatar: "/static/avatar-moderator.svg" },
  ];
  const visibleLimit = 8;
  const visibleMembers = members.slice(0, visibleLimit);
  const overflowCount = Math.max(0, members.length - visibleMembers.length);
  return `
    <div class="live-council" aria-label="회의 원탁">
      <div class="council-table">
        <span>ASSEMBLE</span>
        <strong>${members.length}</strong>
      </div>
      ${visibleMembers.map((member, index) => renderLiveSeat(member, index, visibleMembers.length)).join("")}
      ${overflowCount ? renderLiveOverflowSeat(overflowCount) : ""}
    </div>
  `;
}

function renderLiveSeat(member, index, total) {
  const angle = -90 + (360 / total) * index;
  const avatar = member.avatar
    ? `<img src="${escapeHtml(member.avatar)}" alt="" />`
    : `<span>${escapeHtml(initials(member.label))}</span>`;
  return `
    <div class="live-seat seat-${escapeHtml(member.color)}" style="--angle:${angle}deg">
      <div class="seat-avatar">${avatar}</div>
      <strong>${escapeHtml(member.label)}</strong>
      <small>${escapeHtml(member.title)}</small>
    </div>
  `;
}

function renderLiveOverflowSeat(count) {
  return `
    <div class="live-seat live-seat-overflow">
      <div class="seat-avatar"><span>+${escapeHtml(count)}</span></div>
      <strong>대기열</strong>
      <small>참여자 목록</small>
    </div>
  `;
}

function renderLiveRoster(payload) {
  const roles = payload.meeting.roles || [];
  const synthesis = payload.meeting.moderator_synthesis || {};
  return `
    <aside class="council-roster">
      <div class="roster-head">
        <strong>참여자</strong>
        <span>${roles.length + 2}명 · 에이전트 ${roles.length}</span>
      </div>
      <section class="council-owner">
        <strong>나 <span>Owner</span></strong>
        ${roles.map((role) => {
          const meta = roleMeta[role.id] || { color: "purple", title: role.lens, avatar: "/static/avatar-moderator.svg" };
          return `
            <div class="council-agent">
              <img class="profile profile-tiny" src="${escapeHtml(meta.avatar)}" alt="" />
              <div><strong>${escapeHtml(role.display_name)}</strong><span>${escapeHtml(meta.badge)}</span></div>
              <em></em>
            </div>
          `;
        }).join("")}
      </section>
      <section class="consensus-card">
        <strong>합의도 추이</strong>
        <div class="consensus-score">${escapeHtml(synthesis.confidence || "unknown")}</div>
        <div class="consensus-track"><span></span></div>
        <p>${escapeHtml(synthesis.winner || "판정 대기")}</p>
      </section>
    </aside>
  `;
}

function renderAgent(role) {
  const personality = role.personality || {};
  const tone = personality.tone || role.lens || "";
  const meta = roleMeta[role.id] || { color: "purple", title: role.lens, badge: role.lens };
  return `
    <article class="agent agent-${meta.color}">
      <div class="agent-head">
        <img class="profile profile-small" src="${escapeHtml(meta.avatar)}" alt="" />
        <div class="agent-name">
          <strong>${escapeHtml(role.display_name)}</strong>
          <span>${escapeHtml(meta.title)}</span>
        </div>
        <em>${escapeHtml(meta.badge)}</em>
      </div>
      <span>${escapeHtml(role.id)}</span>
      <small>${escapeHtml(tone)}</small>
    </article>
  `;
}

function renderMessage(message) {
  const meta = roleMeta[message.role_id] || { color: "purple", title: "Moderator", badge: "진행", avatar: "/static/avatar-moderator.svg" };
  const label = message.roundTitle || message.round;
  const stance = stanceLabel(message.stance_status);
  const position = messagePosition(message, state.payload?.meeting);
  return `
    <article class="message message-${meta.color}">
      <img class="profile" src="${escapeHtml(meta.avatar)}" alt="" />
      <div class="message-body">
      <div class="message-header">
        <span class="speaker">
          <strong>${escapeHtml(message.display_name)}</strong>
          <em>${escapeHtml(meta.badge)}</em>
        </span>
        <span class="message-route">전체 · ${escapeHtml(label)} · <span class="confidence">${escapeHtml(message.confidence || "")}</span></span>
      </div>
      ${position ? `<p class="stance-line"><strong>${escapeHtml(stance)}</strong> ${escapeHtml(position)}</p>` : ""}
      <p>${escapeHtml(message.content)}</p>
      </div>
    </article>
  `;
}

function stanceLabel(status) {
  if (status === "changed") return "입장 변화";
  if (status === "softened") return "입장 약화";
  if (status === "strengthened") return "입장 강화";
  return "입장 유지";
}

function renderBoard(payload) {
  const board = document.querySelector("#board");
  const meeting = payload.meeting;
  const researchByRole = Object.fromEntries(
    (meeting.research_artifacts || []).map((artifact) => [artifact.role_id, artifact.path])
  );
  const synthesis = meeting.moderator_synthesis || {};
  const stanceSummary = buildStanceSummary(meeting);
  board.innerHTML = `
    <section class="board-view">
      <div class="room-strip">
        <div>
          <strong>작전판</strong>
          <small>공식 기록을 압축해서 입장, 근거 품질, 충돌 지점을 한눈에 봅니다.</small>
        </div>
        <div class="room-actions">
          <span class="room-status">에이전트 ${(meeting.roles || []).length}</span>
          <span class="room-status room-status-hot">${escapeHtml(synthesis.winner || "판정 대기")}</span>
          <span class="room-status">합의도 ${escapeHtml(synthesis.confidence || "unknown")}</span>
        </div>
      </div>
      <section class="board-command">
        <div class="board-command-copy">
          <span class="room-kicker">decision map</span>
          <strong>${escapeHtml(synthesis.winner || "판정 대기")}</strong>
          <p>${escapeHtml(synthesis.summary || "모더레이터 합성이 아직 없습니다.")}</p>
          <div class="board-command-meta">
            <span>라운드 ${(meeting.debate_rounds || []).length}</span>
            <span>역할 ${(meeting.roles || []).length}</span>
            <span>근거 ${(meeting.research_artifacts || []).length}</span>
          </div>
        </div>
        <div class="board-command-panel">
          <div><strong>흐름</strong><span>어떤 입장이 반복됐고 누가 유지/수정했는지 봅니다.</span></div>
          <div><strong>검증</strong><span>근거 품질과 탈락한 주장을 같이 봅니다.</span></div>
        </div>
      </section>
      <section class="board-dashboard">
        ${renderStanceOverview(stanceSummary, synthesis)}
        ${renderEvidenceOverview(meeting)}
      </section>
      <section class="board-grid">
        ${(meeting.roles || []).map((role) => renderBoardCard(role, payload, researchByRole[role.id])).join("")}
      </section>
      <section class="synthesis">
        <h3>최종 판정</h3>
        <p><strong>${escapeHtml(synthesis.winner || "Undetermined")}</strong> · confidence ${escapeHtml(synthesis.confidence || "")}</p>
        <p>${escapeHtml(synthesis.summary || "")}</p>
        <p>${escapeHtml((synthesis.caveats || []).join(" / "))}</p>
      </section>
    </section>
  `;
}

function buildStanceSummary(meeting) {
  const items = new Map();
  const fallbackPosition = meeting.moderator_synthesis?.winner || "입장 미정";
  for (const round of meeting.debate_rounds || []) {
    for (const message of round.messages || []) {
      const stance = messagePosition(message, meeting, fallbackPosition);
      const item = items.get(stance) || { stance, count: 0, roles: new Set(), statuses: new Map() };
      item.count += 1;
      item.roles.add(message.display_name || message.role_id || "agent");
      const status = stanceLabel(message.stance_status);
      item.statuses.set(status, (item.statuses.get(status) || 0) + 1);
      items.set(stance, item);
    }
  }
  return Array.from(items.values())
    .map((item) => ({
      stance: item.stance,
      count: item.count,
      roles: Array.from(item.roles),
      statuses: Array.from(item.statuses.entries()).sort((a, b) => b[1] - a[1]),
    }))
    .sort((a, b) => b.count - a.count);
}

function messagePosition(message, meeting, fallbackPosition) {
  if (message.position) return message.position;
  const synthesisWinner = fallbackPosition || meeting?.moderator_synthesis?.winner;
  if (synthesisWinner) return synthesisWinner;
  return "입장 미정";
}

function renderStanceOverview(items, synthesis) {
  const total = items.reduce((sum, item) => sum + item.count, 0) || 1;
  return `
    <section class="stance-overview">
      <div class="stance-lead">
        <strong>우세 흐름</strong>
        <span>${escapeHtml(synthesis.winner || "판정 대기")} · ${escapeHtml(synthesis.confidence || "unknown")}</span>
      </div>
      <div class="stance-bars">
        ${
          items.length
            ? items.map((item) => renderStanceBar(item, total)).join("")
            : '<p class="stance-empty">아직 비교할 입장이 없습니다.</p>'
        }
      </div>
    </section>
  `;
}

function renderStanceBar(item, total) {
  const percent = Math.max(8, Math.round((item.count / total) * 100));
  const status = item.statuses[0]?.[0] || "입장 유지";
  return `
    <article class="stance-bar">
      <div>
        <strong>${escapeHtml(item.stance)}</strong>
        <span>${escapeHtml(item.roles.join(", "))} · ${escapeHtml(status)}</span>
      </div>
      <div class="stance-meter" aria-label="${escapeHtml(item.stance)} ${percent}%">
        <span style="width:${percent}%"></span>
      </div>
    </article>
  `;
}

function renderBoardCard(role, payload, researchPath) {
  const meta = roleMeta[role.id] || { color: "purple", title: role.lens, badge: role.lens };
  const researchJson = payload.research_json?.[role.id] || {};
  const rounds = payload.meeting.debate_rounds || [];
  const messages = rounds
    .map((round) => (round.messages || []).find((message) => message.role_id === role.id))
    .filter(Boolean);
  const researchKey = researchPath ? researchPath.replace("private_research/", "").replace(".json", ".md") : "";
  const research = payload.research[researchKey] || "";
  const researchSummary = research.split("## Summary")[1]?.split("## Confidence")[0]?.trim() || "Research summary unavailable.";
  const latestMessage = messages[messages.length - 1] || {};
  const firstMessage = messages[0] || {};
  return `
    <article class="board-card board-${meta.color}">
      <div class="board-card-title">
        <h3>${escapeHtml(role.display_name)}</h3>
        <span>${escapeHtml(meta.badge)}</span>
      </div>
      <p>${escapeHtml(lensLabels[role.lens] || role.lens)} · ${escapeHtml(focusLabels[role.id] || role.research_focus)}</p>
      <div class="board-position">
        <span>현재 입장</span>
        <strong>${escapeHtml(messagePosition(latestMessage, payload.meeting))}</strong>
      </div>
      <div class="board-insight-grid">
        <section>
          <span>핵심 근거</span>
          <p>${escapeHtml(researchSummary)}</p>
        </section>
        <section>
          <span>변화</span>
          <p>${escapeHtml(messages.map((message) => stanceLabel(message.stance_status)).join(" → ") || "기록 없음")}</p>
        </section>
        <section>
          <span>첫 주장</span>
          <p>${escapeHtml(firstMessage.content || "기록 없음")}</p>
        </section>
        <section>
          <span>마지막 반박</span>
          <p>${escapeHtml(latestMessage.content || "기록 없음")}</p>
        </section>
      </div>
      <div class="stance-mini">
        ${messages.map((message) => `<span>${escapeHtml(roundLabel(payload.meeting, message.round, message.round))}: ${escapeHtml(stanceLabel(message.stance_status))}</span>`).join("")}
      </div>
      ${renderEvidenceTable(researchJson)}
    </article>
  `;
}

function renderEvidenceOverview(meeting) {
  const gate = meeting.evidence_gate || {};
  return `
    <section class="evidence-overview">
      <div>
        <strong>Evidence Gate</strong>
        <span class="status-pill status-${escapeHtml(gate.status || "unknown")}">${escapeHtml(gate.status || "unknown")}</span>
      </div>
      ${renderMetric("지원", gate.total_supported_claims)}
      ${renderMetric("약함", gate.total_weak_claims)}
      ${renderMetric("미지원", gate.total_unsupported_claims)}
      ${renderMetric("탈락", gate.total_verifier_rejected_claims)}
    </section>
  `;
}

function renderMetric(label, value) {
  return `<div class="metric"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value ?? 0)}</strong></div>`;
}

function renderEvidenceTable(research) {
  const gate = research.evidence_gate || {};
  const rows = [
    ["지원", gate.supported_claim_count || 0, "supported"],
    ["약함", gate.weak_claim_count || 0, "weak"],
    ["미지원", gate.unsupported_claim_count || 0, "unsupported"],
    ["탈락", gate.verifier_rejected_claim_count || 0, "rejected"],
  ];
  const failures = gate.failures || [];
  return `
    <div class="evidence-table">
      <div class="evidence-head">
        <strong>근거 검증</strong>
        <span class="status-pill status-${escapeHtml(gate.status || "unknown")}">${escapeHtml(gate.status || "unknown")}</span>
      </div>
      <div class="evidence-counts">
        ${rows.map(([label, count, kind]) => `<span class="count-${kind}"><strong>${escapeHtml(count)}</strong>${escapeHtml(label)}</span>`).join("")}
      </div>
      <div class="evidence-detail">
        <span>출처 ${escapeHtml(gate.source_count || 0)}</span>
        <span>신뢰도 ${escapeHtml(gate.confidence_after || research.confidence || "unknown")}</span>
      </div>
      ${failures.length ? `<ul class="evidence-failures">${failures.map((failure) => `<li>${escapeHtml(failure)}</li>`).join("")}</ul>` : ""}
      ${renderEvidenceClaims("지원 근거", research.claim_evidence || [], "supported")}
      ${renderEvidenceClaims("약한 근거", research.weak_claims || [], "weak")}
      ${renderEvidenceClaims("미지원 근거", research.unsupported_claims || [], "unsupported")}
      ${renderEvidenceClaims("검증 탈락", research.verifier_rejected_claims || [], "rejected")}
    </div>
  `;
}

function renderEvidenceClaims(title, claims, kind) {
  const preview = claims.slice(0, 2);
  if (!preview.length) return "";
  return `
    <details class="claim-group claim-${kind}">
      <summary>${escapeHtml(title)} · ${claims.length}</summary>
      ${preview.map(renderClaim).join("")}
      ${claims.length > preview.length ? `<p class="claim-more">+${claims.length - preview.length} more in archive</p>` : ""}
    </details>
  `;
}

function renderClaim(claim) {
  const urls = claim.evidence || claim.sources || [];
  return `
    <div class="claim-row">
      <strong>${escapeHtml(claim.claim || "")}</strong>
      ${claim.reason ? `<span>${escapeHtml(claim.reason)}</span>` : ""}
      ${urls.length ? `<small>${urls.map((url) => escapeHtml(url)).join(" · ")}</small>` : ""}
    </div>
  `;
}

function renderArchive(payload) {
  const archive = document.querySelector("#archive");
  const entries = buildArchiveEntries(payload);
  if (!entries[state.archiveKey]) state.archiveKey = Object.keys(entries)[0];
  const currentDocument = entries[state.archiveKey] || "";
  archive.innerHTML = `
    <section class="archive-view">
    <div class="room-strip">
      <div>
        <strong>아카이브</strong>
        <small>회의 산출물, 인수인계 기록, 에이전트별 자료를 검토합니다.</small>
      </div>
      <div class="room-actions">
        <span class="room-status">${escapeHtml(Object.keys(entries).length)}개 문서</span>
        <span class="room-status room-status-hot">${escapeHtml(archiveKindLabel(state.archiveKey))}</span>
      </div>
    </div>
    <div class="archive-layout">
      <aside class="archive-list">
        <div class="archive-head">
          <strong>문서 목록</strong>
          <span>회의 산출물과 인수인계 기록</span>
        </div>
        ${renderArchiveGroups(payload, entries)}
      </aside>
      <section class="archive-document">
        <div class="archive-document-head">
          <div>
            <strong>${escapeHtml(state.archiveKey || "문서")}</strong>
            <small>${escapeHtml(archiveOwnerLabel(state.archiveKey, payload))} · ${escapeHtml(documentStat(currentDocument))}</small>
          </div>
          <div class="archive-actions">
            <span>${escapeHtml(archiveOwnerLabel(state.archiveKey, payload))}</span>
            <span>${escapeHtml(archiveKindLabel(state.archiveKey))}</span>
            <button type="button" data-archive-command="copy">복사</button>
            <button type="button" data-archive-command="download">내보내기</button>
          </div>
        </div>
        <pre class="archive-preview">${escapeHtml(currentDocument)}</pre>
      </section>
    </div>
    </section>
  `;
  archive.querySelectorAll("[data-archive]").forEach((button) => {
    button.addEventListener("click", () => {
      state.archiveKey = button.dataset.archive;
      renderArchive(payload);
    });
  });
  archive.querySelectorAll("[data-archive-command]").forEach((button) => {
    button.addEventListener("click", () => handleArchiveCommand(button.dataset.archiveCommand, state.archiveKey, currentDocument, button));
  });
}

function documentStat(value) {
  const text = String(value || "");
  const lines = text ? text.split("\n").length : 0;
  return `${lines} lines · ${text.length} chars`;
}

async function handleArchiveCommand(command, key, content, button) {
  if (command === "copy") {
    const copied = await copyText(content);
    showArchiveCommandFeedback(button, copied ? "복사됨" : "복사 실패", copied ? "success" : "error");
    return;
  }
  if (command === "download") {
    const blob = new Blob([content], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = archiveDownloadName(key);
    document.body.append(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    showArchiveCommandFeedback(button, "내보냄", "success");
  }
}

async function copyText(content) {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(content);
      return true;
    } catch {
      return copyTextWithTextarea(content);
    }
  }
  return copyTextWithTextarea(content);
}

function copyTextWithTextarea(content) {
  const textarea = document.createElement("textarea");
  textarea.value = content;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  document.body.append(textarea);
  textarea.select();
  const copied = document.execCommand?.("copy") || false;
  textarea.remove();
  return copied;
}

function showArchiveCommandFeedback(button, label, tone = "success") {
  if (!button) return;
  const original = button.textContent;
  button.textContent = label;
  button.classList.add(tone === "error" ? "is-error" : "is-confirmed");
  setTimeout(() => {
    button.textContent = original;
    button.classList.remove("is-confirmed", "is-error");
  }, 1400);
}

function archiveDownloadName(key) {
  return String(key || "archive.md").split("/").pop() || "archive.md";
}

function archiveKindLabel(key) {
  if (!key) return "기록";
  if (key.includes("research/")) return "리서치";
  if (key.includes("tasks/")) return "작업 배정";
  if (key.includes("return_packets/")) return "세션 복귀";
  if (key === "decision.md") return "결정";
  if (key === "transcript.md") return "회의록";
  if (key === "agenda.md") return "안건";
  return "기록";
}

function archiveOwnerLabel(key, payload) {
  if (!key) return "공용 기록";
  const roles = payload?.meeting?.roles || [];
  const role = roles.find((candidate) => key.includes(`/${candidate.id}/`) || key.endsWith(`/${candidate.id}.md`));
  if (role) return role.display_name;
  return "공용 기록";
}

function buildArchiveEntries(payload) {
  return {
    ...payload.artifacts,
    ...Object.fromEntries(Object.entries(payload.tasks).map(([key, value]) => [`tasks/${key}`, value])),
    ...Object.fromEntries(Object.entries(payload.return_packets || {}).map(([key, value]) => [`return_packets/${key}`, value])),
    ...Object.fromEntries(Object.entries(payload.research).map(([key, value]) => [`research/${key}`, value])),
  };
}

function renderArchiveGroups(payload, entries) {
  const publicKeys = ["agenda.md", "transcript.md", "decision.md", "meeting.json"].filter((key) => key in entries);
  const roleGroups = (payload.meeting.roles || []).map((role) => {
    const meta = roleMeta[role.id] || { color: "purple", title: role.lens, badge: role.lens, avatar: "/static/avatar-moderator.svg" };
    const keys = Object.keys(entries)
      .filter((key) => key.includes(`/${role.id}/`) || key.endsWith(`/${role.id}.md`))
      .sort();
    return { role, meta, keys };
  });

  return [
    renderArchiveGroup("공용 기록", "회의 전체 문서", publicKeys, entries),
    ...roleGroups.map(({ role, meta, keys }) =>
      renderArchiveGroup(role.display_name, meta.badge, keys, entries, meta)
    ),
  ].join("");
}

function renderArchiveGroup(title, subtitle, keys, entries, meta) {
  if (!keys.length) return "";
  const avatar = meta
    ? `<img class="profile profile-tiny" src="${escapeHtml(meta.avatar)}" alt="" />`
    : `<span class="archive-dot"></span>`;
  return `
    <section class="archive-group">
      <div class="archive-group-title">
        ${avatar}
        <div>
          <strong>${escapeHtml(title)}</strong>
          <span>${escapeHtml(subtitle)}</span>
        </div>
      </div>
      ${keys.map((key) => renderArchiveButton(key, entries)).join("")}
    </section>
  `;
}

function renderArchiveButton(key, entries) {
  const label = key
    .replace("research/", "")
    .replace("tasks/", "task · ")
    .replace("/research.md", " · research.md");
  return `
    <button type="button" class="${key === state.archiveKey ? "is-active" : ""}" data-archive="${escapeHtml(key)}">
      <strong>${escapeHtml(label)}</strong>
      <span>${escapeHtml(archiveKindLabel(key))}</span>
    </button>
  `;
}

tabs.forEach((tab) => {
  tab.addEventListener("click", () => {
    setActiveTab(tab.dataset.tab);
    render();
  });
  tab.addEventListener("keydown", (event) => {
    const nextTab = tabForKeyboardEvent(event, tab);
    if (!nextTab) return;
    event.preventDefault();
    nextTab.focus();
    setActiveTab(nextTab.dataset.tab);
    render();
  });
});

function tabForKeyboardEvent(event, currentTab) {
  const tabList = Array.from(tabs);
  const currentIndex = tabList.indexOf(currentTab);
  if (event.key === "Home") return tabList[0];
  if (event.key === "End") return tabList[tabList.length - 1];
  if (event.key === "ArrowRight") return tabList[(currentIndex + 1) % tabList.length];
  if (event.key === "ArrowLeft") return tabList[(currentIndex - 1 + tabList.length) % tabList.length];
  return null;
}

uiScale?.addEventListener("input", () => {
  localStorage.setItem("agentsassemble.uiScale", uiScale.value);
  applyScaleSettings();
});

textScale?.addEventListener("input", () => {
  localStorage.setItem("agentsassemble.textScale", textScale.value);
  applyScaleSettings();
});

runDemo.addEventListener("click", async () => {
  runDemo.disabled = true;
  runDemo.setAttribute("aria-busy", "true");
  runDemo.textContent = "Running...";
  showAppStatus("Mock Demo 실행 중", "info");
  try {
    const result = await fetchJson("/api/demo", { method: "POST" });
    await loadMeetings();
    meetingSelect.value = result.meeting_id;
    await loadMeeting(result.meeting_id);
    showAppStatus("Mock Demo 생성 완료", "success");
  } catch (error) {
    showAppStatus(error?.message || "Mock Demo 실행 실패", "error");
  } finally {
    runDemo.disabled = false;
    runDemo.removeAttribute("aria-busy");
    runDemo.textContent = "Mock Demo";
  }
});

meetingSelect.addEventListener("change", () => {
  if (meetingSelect.value) loadMeeting(meetingSelect.value);
});

(async function init() {
  applyScaleSettings();
  await loadLobby();
  const latest = await loadMeetings();
  await loadMeeting(latest);
  setInterval(loadLobby, 4000);
})();
