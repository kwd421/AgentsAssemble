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
  tabs.forEach((tab) => tab.classList.toggle("is-active", tab.dataset.tab === tabId));
  panels.forEach((panel) => panel.classList.toggle("is-active", panel.id === tabId));
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
  lobby.innerHTML = `
    <section class="lobby-layout">
      <div class="lobby-panel">
        <div class="lobby-title">
          <strong>멀티 로비</strong>
          <span>공식 회의록과 분리된 대기실입니다.</span>
        </div>
        <form id="lobby-form" class="lobby-form">
          <select id="lobby-side" aria-label="보내는 쪽">
            ${renderLobbySideOptions()}
          </select>
          <input id="lobby-name" maxlength="32" placeholder="이름" value="${escapeHtml(localStorage.getItem("agentsassemble.name") || "")}" />
          <input id="lobby-message" maxlength="240" placeholder="짧은 메시지" />
          <button type="submit">보내기</button>
          <button type="button" id="lobby-ready">준비</button>
          <button type="button" id="lobby-deploy">Deploy</button>
        </form>
        <div class="lobby-feed">
          ${state.lobbyEvents.length ? state.lobbyEvents.map(renderLobbyEvent).join("") : '<p class="lobby-empty">아직 로비 메시지가 없습니다.</p>'}
        </div>
      </div>
    </section>
  `;
  const form = lobby.querySelector("#lobby-form");
  const ready = lobby.querySelector("#lobby-ready");
  const deploy = lobby.querySelector("#lobby-deploy");
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    await sendLobbyEvent("message");
  });
  ready.addEventListener("click", () => sendLobbyEvent("ready"));
  deploy.addEventListener("click", () => sendLobbyEvent("deploy"));
}

function renderLobbyEvent(event) {
  const currentName = localStorage.getItem("agentsassemble.name") || "";
  const storedSide = lobbySides.has(event.side) ? event.side : "";
  const side = storedSide || (currentName && event.name === currentName ? "mine" : "other");
  return `
    <article class="lobby-event lobby-${escapeHtml(event.kind || "message")} lobby-${side}">
      <div class="lobby-avatar">${escapeHtml(initials(event.name || "G"))}</div>
      <div class="lobby-bubble">
        <div class="lobby-meta">
          <strong>${escapeHtml(event.name || "guest")}</strong>
          <span>${escapeHtml(lobbySideLabel(side))}</span>
          <span>${escapeHtml(lobbyKindLabel(event.kind))}</span>
        </div>
        <p>${escapeHtml(event.message || "")}</p>
      </div>
    </article>
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
  if (kind === "deploy") return "deploy";
  return "메시지";
}

async function sendLobbyEvent(kind) {
  const sideInput = document.querySelector("#lobby-side");
  const nameInput = document.querySelector("#lobby-name");
  const messageInput = document.querySelector("#lobby-message");
  const side = lobbySides.has(sideInput?.value) ? sideInput.value : "mine";
  const name = nameInput?.value.trim() || defaultLobbyName(side);
  const message = messageInput?.value.trim() || "";
  if (kind === "message" && !message) return;
  localStorage.setItem("agentsassemble.lobbySide", side);
  if (side === "mine") localStorage.setItem("agentsassemble.name", name);
  const payload = await fetchJson("/api/lobby", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, side, kind, message }),
  });
  state.lobbyEvents = payload.events || [];
  if (messageInput) messageInput.value = "";
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
  const messages = rounds.flatMap((round) =>
    (round.messages || []).map((message) => ({ ...message, roundTitle: roundLabel(payload.meeting, round.id, round.title) }))
  );
  const synthesis = payload.meeting.moderator_synthesis || {};
  live.innerHTML = `
    <div class="live-layout">
      <aside class="agent-rail">
        ${roles.map(renderAgent).join("")}
      </aside>
      <div class="message-list">
        <p class="event">회의 시작 · ${escapeHtml(displayQuestion(payload.meeting.question))}</p>
        <p class="event">독립 리서치 완료 · Round 1 진입</p>
        ${messages.map(renderMessage).join("")}
        <article class="message message-purple">
          <img class="profile" src="/static/avatar-moderator.svg" alt="" />
          <div class="message-body">
          <div class="message-header"><span class="speaker"><strong>Moderator</strong><em>종합</em></span><span class="confidence">${escapeHtml(synthesis.confidence || "")}</span></div>
          <p>${escapeHtml(synthesis.summary || "")}</p>
          </div>
        </article>
        <p class="event">결정 생성 · ${escapeHtml(synthesis.winner || "Undetermined")}</p>
      </div>
    </div>
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
  return `
    <article class="message message-${meta.color}">
      <img class="profile" src="${escapeHtml(meta.avatar)}" alt="" />
      <div class="message-body">
      <div class="message-header">
        <span class="speaker">
          <strong>${escapeHtml(message.display_name)}</strong>
          <em>${escapeHtml(meta.badge)}</em>
        </span>
        <span>${escapeHtml(label)} · <span class="confidence">${escapeHtml(message.confidence || "")}</span></span>
      </div>
      ${message.position ? `<p class="stance-line"><strong>입장</strong> ${escapeHtml(message.position)} · ${escapeHtml(message.stance_status || "held")}</p>` : ""}
      <p>${escapeHtml(message.content)}</p>
      </div>
    </article>
  `;
}

function renderBoard(payload) {
  const board = document.querySelector("#board");
  const meeting = payload.meeting;
  const researchByRole = Object.fromEntries(
    (meeting.research_artifacts || []).map((artifact) => [artifact.role_id, artifact.path])
  );
  const synthesis = meeting.moderator_synthesis || {};
  board.innerHTML = `
    <section class="board-legend">
      <div><strong>작전판</strong><span>각 에이전트가 어떤 관점으로 봤고, 어디서 같은 결론/다른 근거가 나왔는지 정리합니다.</span></div>
      <div><strong>판정</strong><span>${escapeHtml(synthesis.winner || "미정")} · ${escapeHtml(synthesis.confidence || "unknown")}</span></div>
    </section>
    ${renderEvidenceOverview(meeting)}
    <section class="board-grid">
      ${(meeting.roles || []).map((role) => renderBoardCard(role, payload, researchByRole[role.id])).join("")}
    </section>
    <section class="synthesis">
      <h3>최종 판정</h3>
      <p><strong>${escapeHtml(synthesis.winner || "Undetermined")}</strong> · confidence ${escapeHtml(synthesis.confidence || "")}</p>
      <p>${escapeHtml(synthesis.summary || "")}</p>
      <p>${escapeHtml((synthesis.caveats || []).join(" / "))}</p>
    </section>
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
  return `
    <article class="board-card board-${meta.color}">
      <div class="board-card-title">
        <h3>${escapeHtml(role.display_name)}</h3>
        <span>${escapeHtml(meta.badge)}</span>
      </div>
      <p>${escapeHtml(lensLabels[role.lens] || role.lens)} · ${escapeHtml(focusLabels[role.id] || role.research_focus)}</p>
      ${renderEvidenceTable(researchJson)}
      <p><strong>리서치 요약</strong><br>${escapeHtml(researchSummary)}</p>
      ${messages.map((message) => `<p><strong>${escapeHtml(roundLabel(payload.meeting, message.round, message.round))}</strong><br>${message.position ? `입장: ${escapeHtml(message.position)} · ${escapeHtml(message.stance_status || "held")}<br>` : ""}${escapeHtml(message.content)}</p>`).join("")}
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
  archive.innerHTML = `
    <div class="archive-layout">
      <aside class="archive-list">
        ${renderArchiveGroups(payload, entries)}
      </aside>
      <pre class="archive-preview">${escapeHtml(entries[state.archiveKey] || "")}</pre>
    </div>
  `;
  archive.querySelectorAll("[data-archive]").forEach((button) => {
    button.addEventListener("click", () => {
      state.archiveKey = button.dataset.archive;
      renderArchive(payload);
    });
  });
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
  return `<button type="button" class="${key === state.archiveKey ? "is-active" : ""}" data-archive="${escapeHtml(key)}">${escapeHtml(label)}</button>`;
}

tabs.forEach((tab) => {
  tab.addEventListener("click", () => {
    setActiveTab(tab.dataset.tab);
    render();
  });
});

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
  runDemo.textContent = "Running...";
  try {
    const result = await fetchJson("/api/demo", { method: "POST" });
    await loadMeetings();
    meetingSelect.value = result.meeting_id;
    await loadMeeting(result.meeting_id);
  } finally {
    runDemo.disabled = false;
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
