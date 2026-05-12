import { bindingSummary, displayQuestion, escapeHtml, fetchJson, focusLabels, lensLabels, roleMeta, roundLabel, setSideChatEvents, state } from "./shared.js";

export function renderLive(payload, options = {}) {
  const roles = payload.meeting.roles || [];
  const rounds = payload.meeting.debate_rounds || [];
  const live = document.querySelector("#live");
  const sideChatFocused = document.activeElement?.id === "side-chat-message";
  const sideChatDraft = live?.querySelector("#side-chat-message")?.value || "";
  const shouldFollowLatest = options.followLatest || isLiveTranscriptNearBottom(live);
  const messages = rounds.flatMap((round) =>
    (round.messages || []).map((message) => ({ ...message, roundTitle: roundLabel(payload.meeting, round.id, round.title) }))
  );
  const liveEvents = payload.live_events || [];
  const officialLiveEvents = liveEvents.filter(isOfficialLiveItem);
  const systemLiveEvents = liveEvents.filter((event) => !isOfficialLiveItem(event));
  const liveMessages = liveEvents.length ? officialLiveEvents : messages;
  const liveOverviewItems = liveEvents.length ? liveEvents : messages;
  const synthesis = payload.meeting.moderator_synthesis || {};
  const gate = payload.meeting.decision_gate || {};
  const isComplete = payload.meeting.live_status === "complete" || Boolean(synthesis.winner);
  const roundStatus = liveStatusLabel(payload.meeting, rounds, isComplete);
  live.innerHTML = `
    <div class="live-room">
      <section class="live-chat-header">
        <div>
          <div class="live-statusbar">
            <span class="live-pill">공식 실황</span>
            <strong>${roundStatus}</strong>
            <span>결정 ${escapeHtml(decisionGateLabel(gate.status))}</span>
          </div>
          <div class="live-chat-title">
            <h2>${escapeHtml(displayQuestion(payload.meeting.question))}</h2>
            <div class="channel-tabs" aria-label="발언 대상">
              <span class="is-active">전체</span>
              <span>팀</span>
              <span>귓속말</span>
            </div>
          </div>
        </div>
      </section>
      ${renderLiveOverview(payload, liveOverviewItems)}
      <section class="live-chat-room">
        ${renderSystemEventStack(systemLiveEvents)}
        <main class="message-list live-transcript live-chat-feed" aria-label="공식 토론 기록" aria-live="polite">
          <button type="button" class="latest-jump" hidden>최신으로 가기</button>
          <div class="feed-head">
            <div>
              <strong>공식 토론</strong>
              <span>이 영역의 발언은 transcript.md와 decision.md의 근거가 됩니다.</span>
            </div>
            <em class="record-badge">공식 기록</em>
          </div>
          ${liveMessages.map(renderLiveItem).join("")}
          ${synthesis.summary ? `<article class="message message-purple message-moderator">
            <img class="profile" src="/static/avatar-moderator.svg" alt="" />
            <div class="message-body">
            <div class="message-header"><span class="speaker"><strong>진행자</strong><em>종합</em></span><span class="message-route">전체 · <span class="confidence">${escapeHtml(confidenceLabel(synthesis.confidence))}</span></span></div>
            ${renderTextBlocks(userVisibleSummary(synthesis.summary || ""), { highlight: synthesis.winner })}
            </div>
          </article>` : ""}
        </main>
        <aside class="live-chat-side">
          ${renderSideChat()}
          ${renderLiveOutcome(payload, liveOverviewItems)}
          ${renderOfficialRoster(roles)}
          ${renderLiveTimeline(payload, liveOverviewItems)}
        </aside>
      </section>
    </div>
  `;
  bindLatestJump(live);
  bindSideChat(live);
  const input = live.querySelector("#side-chat-message");
  if (input && sideChatFocused) {
    input.value = sideChatDraft;
    input.focus();
  } else if (input && sideChatDraft) {
    input.value = sideChatDraft;
  }
  if (!sideChatFocused) scrollSideChatToLatest(live);
  if (shouldFollowLatest) scrollLiveTranscriptToLatest(live);
  updateLatestJump(live);
}

export function refreshSideChatFeed() {
  const live = document.querySelector("#live");
  const panel = live?.querySelector(".side-chat-panel");
  if (!panel) return;
  const focused = document.activeElement?.id === "side-chat-message";
  const draft = live.querySelector("#side-chat-message")?.value || "";
  const shouldFollowLatest = isSideChatFeedNearBottom(live);
  panel.outerHTML = renderSideChat();
  bindSideChat(live);
  const input = live.querySelector("#side-chat-message");
  if (input && draft) {
    input.value = draft;
  }
  if (input && focused) {
    input.focus();
  }
  if (shouldFollowLatest) scrollSideChatToLatest(live);
}

function renderSideChat() {
  const events = state.sideChatEvents || [];
  return `
    <aside class="side-chat-panel" aria-label="비공식 채팅">
      <div class="side-chat-head">
        <strong>비공식 채팅</strong>
        <span>공식 회의록에 들어가지 않습니다.</span>
      </div>
      <div class="side-chat-feed">
        ${events.length ? events.map(renderSideChatEvent).join("") : '<p class="side-chat-empty">아직 비공식 채팅이 없습니다.</p>'}
      </div>
      <form id="side-chat-form" class="side-chat-form">
        <input id="side-chat-message" maxlength="240" placeholder="실황 보면서 한마디" />
        <button type="submit">전송</button>
      </form>
    </aside>
  `;
}

function renderSideChatEvent(event) {
  return `
    <article class="side-chat-event side-${escapeHtml(event.side || "other")}">
      <strong>${escapeHtml(event.name || "guest")}</strong>
      <p>${escapeHtml(event.message || "")}</p>
    </article>
  `;
}

function bindSideChat(root) {
  const form = root?.querySelector("#side-chat-form");
  if (!form) return;
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    await sendSideChatMessage(root);
  });
  root.querySelector("#side-chat-message")?.addEventListener("keydown", async (event) => {
    if (event.key !== "Enter" || event.isComposing) return;
    event.preventDefault();
    await sendSideChatMessage(root);
  });
}

async function sendSideChatMessage(root) {
  const input = root.querySelector("#side-chat-message");
  const previousValue = input?.value || "";
  const message = previousValue.trim();
  if (!message) return;
  if (input) input.value = "";
  try {
    const payload = await fetchJson("/api/side-chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: localStorage.getItem("agentsassemble.name") || "나",
        side: "mine",
        kind: "message",
        message,
      }),
    });
    setSideChatEvents(payload.events || []);
    renderLive(state.payload, { followLatest: false });
    root.querySelector("#side-chat-message")?.focus();
  } catch (error) {
    const activeInput = root.querySelector("#side-chat-message");
    if (activeInput) {
      activeInput.value = previousValue;
      activeInput.focus();
    }
    throw error;
  }
}

function isSideChatFeedNearBottom(root) {
  const feed = root?.querySelector(".side-chat-feed");
  if (!feed) return true;
  const distanceFromBottom = feed.scrollHeight - feed.scrollTop - feed.clientHeight;
  return distanceFromBottom < 48;
}

function scrollSideChatToLatest(root) {
  const feed = root?.querySelector(".side-chat-feed");
  if (!feed) return;
  requestAnimationFrame(() => {
    feed.scrollTop = feed.scrollHeight;
  });
}

function renderLiveItem(item) {
  if (item.kind === "status" || item.kind === "artifact") return renderSystemLine(item);
  if (item.kind === "research") return renderResearchEvent(item);
  if (item.kind && item.kind !== "message") return renderLiveEvent(item);
  return renderMessage(item);
}

function isOfficialLiveItem(item) {
  return Boolean(item.official_record) || item.kind === "message" || item.kind === "synthesis";
}

function renderSystemEventStack(events) {
  if (!events.length) return "";
  return `
    <section class="system-event-stack" aria-label="진행 로그">
      <header>
        <strong>진행 로그</strong>
        <span>공식 기록 제외 · 상태/리서치/산출물</span>
      </header>
      <div class="system-event-list">
        ${events.map(renderLiveItem).join("")}
      </div>
    </section>
  `;
}

function renderLiveEvent(event) {
  const meta = roleMeta[event.role_id] || { color: event.role_id === "moderator" ? "purple" : "cyan", badge: event.kind || "진행", avatar: "/static/avatar-moderator.svg" };
  const displayName = displayNameLabel(event);
  const route = event.round ? `${roundKindLabel(event.round)} · ${eventKindLabel(event.kind)}` : eventKindLabel(event.kind);
  return `
    <article class="message message-${escapeHtml(meta.color)} live-event-bubble">
      <img class="profile" src="${escapeHtml(meta.avatar)}" alt="" />
      <div class="message-body">
        <div class="message-header">
          <span class="speaker">
            <strong>${escapeHtml(displayName)}</strong>
            <em>${escapeHtml(meta.badge || event.kind || "진행")}</em>
          </span>
          <span class="message-route">${escapeHtml(route)} · <span class="confidence">${escapeHtml(confidenceLabel(event.confidence))}</span></span>
        </div>
        ${event.position ? `<p class="stance-line"><strong>${escapeHtml(stanceLabel(event.stance_status))}</strong> ${escapeHtml(event.position)}</p>` : ""}
        ${renderTextBlocks(userVisibleSummary(event.content || ""), { highlight: event.position })}
      </div>
    </article>
  `;
}

function renderSystemLine(event) {
  const name = displayNameLabel(event);
  const content = userVisibleSummary(event.content || "");
  const showName = name !== "시스템" && !content.includes(name);
  return `
    <div class="system-line" role="status">
      <span>${escapeHtml(eventKindLabel(event.kind))}</span>
      ${showName ? `<strong>${escapeHtml(name)}</strong>` : ""}
      <p>${escapeHtml(content)}</p>
    </div>
  `;
}

function renderResearchEvent(event) {
  const meta = roleMeta[event.role_id] || { color: "cyan", badge: "리서치", avatar: "/static/avatar-moderator.svg" };
  const displayName = displayNameLabel(event);
  return `
    <details class="research-card research-${escapeHtml(meta.color)}">
      <summary>
        <img class="profile profile-tiny" src="${escapeHtml(meta.avatar)}" alt="" />
        <span><strong>${escapeHtml(displayName)}</strong><em>리서치 요약 · ${escapeHtml(confidenceLabel(event.confidence))}${renderRetryBadge(event)}</em></span>
      </summary>
      <div>${renderTextBlocks(event.content || "")}</div>
    </details>
  `;
}

function renderRetryBadge(event) {
  if (!event.retry_status || event.retry_status === "not_needed") return "";
  const label = event.retry_status === "recovered" ? "재시도 회복" : "재시도 실패";
  const attempts = event.retry_attempts ? ` ${event.retry_attempts}회` : "";
  return ` · ${label}${attempts}`;
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
    updateLatestJump(live);
  });
}

function bindLatestJump(live) {
  const feed = live?.querySelector(".live-transcript");
  const button = live?.querySelector(".latest-jump");
  if (!feed || !button) return;
  button.addEventListener("click", () => scrollLiveTranscriptToLatest(live));
  feed.addEventListener("scroll", () => updateLatestJump(live), { passive: true });
}

function updateLatestJump(live) {
  const button = live?.querySelector(".latest-jump");
  if (!button) return;
  button.hidden = isLiveTranscriptNearBottom(live);
}

function renderLiveTimeline(payload, messages) {
  const rounds = payload.meeting.debate_rounds || [];
  const counts = liveEventCounts(messages);
  return `
    <aside class="live-timeline">
      <strong>진행</strong>
      <ol>
        <li class="is-done"><span></span>회의 시작</li>
        <li class="is-done"><span></span>독립 리서치</li>
        <li class="${isMeetingComplete(payload.meeting) ? "is-done" : "is-current"}"><span></span>${escapeHtml(rounds.length ? `${rounds.length}라운드` : "라운드 대기")}</li>
        <li class="${isMeetingComplete(payload.meeting) ? "is-done" : ""}"><span></span>결정 생성</li>
      </ol>
      ${renderRailMetric("공식 발언", counts.messages)}
      ${renderRailMetric("리서치", counts.research)}
      ${renderRailMetric("라운드", `${rounds.length || 0} / ${(payload.meeting.meeting_template?.rounds || []).length}`)}
    </aside>
  `;
}

function renderLiveOverview(payload, messages) {
  const roles = payload.meeting.roles || [];
  const rounds = payload.meeting.debate_rounds || [];
  const synthesis = payload.meeting.moderator_synthesis || {};
  const gate = payload.meeting.decision_gate || {};
  const counts = liveEventCounts(messages);
  return `
    <section class="live-overview-strip" aria-label="실황 요약">
      ${renderOverviewCard("판정", synthesis.winner || "판정 대기", decisionGateLabel(gate.status))}
      ${renderOverviewCard("참여자", `${roles.length}명`, roles.map((role) => role.display_name).join(" · ") || "대기")}
      ${renderOverviewCard("진행", rounds.length ? `${rounds.length}라운드` : "라운드 대기", `공식 발언 ${counts.messages}개 · 리서치 ${counts.research}개`)}
      ${renderOverviewCard("비공식", `${(state.sideChatEvents || []).length}개`, "회의록 제외")}
    </section>
  `;
}

function renderOverviewCard(label, value, detail) {
  return `
    <div class="overview-card">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(value)}</strong>
      <small>${escapeHtml(detail || "")}</small>
    </div>
  `;
}

function renderLiveOutcome(payload, messages) {
  const synthesis = payload.meeting.moderator_synthesis || {};
  const gate = payload.meeting.decision_gate || {};
  const counts = liveEventCounts(messages);
  return `
    <aside class="live-outcome">
      <div class="outcome-card">
        <span>현재 판정</span>
        <strong>${escapeHtml(synthesis.winner || "판정 대기")}</strong>
        ${renderTextBlocks(userVisibleSummary(synthesis.summary || "아직 종합 의견이 없습니다."), { highlight: synthesis.winner })}
      </div>
      <div class="consensus-card">
        <strong>결정 상태</strong>
        <div class="consensus-score">${escapeHtml(decisionGateLabel(gate.status))}</div>
        <div class="consensus-track"><span></span></div>
        <p>${escapeHtml(gate.can_finalize ? "확정 가능" : "보류")} · 공식 발언 ${escapeHtml(counts.messages)}개 기반</p>
      </div>
      ${renderDecisionGateCard(payload.meeting)}
      <section class="rail-card rail-compact">
        <strong>최근 산출물</strong>
        ${renderArtifactRow("결정안", "decision.md")}
        ${renderArtifactRow("발언 로그", "transcript.md")}
        ${renderArtifactRow("의제", "agenda.md")}
      </section>
    </aside>
  `;
}

function renderDecisionGateCard(meeting) {
  const gate = meeting.decision_gate || {};
  return `
    <section class="rail-card rail-compact decision-gate-card">
      <strong>Decision Gate</strong>
      <div class="artifact-row"><span>상태</span><em>${escapeHtml(decisionGateLabel(gate.status))}</em></div>
      <div class="artifact-row"><span>다음</span><em>${escapeHtml(decisionActionLabel(gate.required_action))}</em></div>
      ${(gate.reasons || []).slice(0, 3).map((reason) => `<p>${escapeHtml(reason)}</p>`).join("")}
    </section>
  `;
}

function liveStatusLabel(meeting, rounds, isComplete) {
  if (meeting.live_status === "stalled") return "중단됨";
  if (isComplete) return "회의 완료";
  return `Round ${escapeHtml(rounds.length || 0)}`;
}

function liveEventCounts(items) {
  return items.reduce(
    (counts, item) => {
      const kind = item.kind || "message";
      if (kind === "message") counts.messages += 1;
      else if (kind === "research") counts.research += 1;
      else if (kind === "status") counts.status += 1;
      else if (kind === "synthesis") counts.synthesis += 1;
      return counts;
    },
    { messages: 0, research: 0, status: 0, synthesis: 0 }
  );
}

function renderOfficialRoster(roles) {
  return `
    <aside class="official-roster">
      <div class="roster-head">
        <strong>본회의 참여자</strong>
        <span>공식 발언 권한 · 에이전트 ${roles.length}</span>
      </div>
      ${roles.map((role) => {
        const meta = roleMeta[role.id] || { color: "purple", title: role.lens, badge: role.lens, avatar: "/static/avatar-moderator.svg" };
        return `
          <div class="official-speaker official-${escapeHtml(meta.color)}">
            <img class="profile profile-tiny" src="${escapeHtml(meta.avatar)}" alt="" />
            <div>
              <strong>${escapeHtml(role.display_name)}</strong>
              <span>${escapeHtml(meta.badge)} · ${escapeHtml(providerLabel(role))}</span>
              <small>${escapeHtml(agentLabel(role))}</small>
            </div>
          </div>
        `;
      }).join("")}
    </aside>
  `;
}

function providerLabel(role) {
  const { binding, provider } = bindingSummary(state.payload?.meeting, role.id);
  return provider?.display_name || binding?.provider_id || "provider 없음";
}

function agentLabel(role) {
  const { binding, permissions } = bindingSummary(state.payload?.meeting, role.id);
  const mode = permissions?.implementation ? "implementation" : "meeting read-only";
  return `${binding?.agent_id || "unbound"} · ${mode}`;
}

function renderRailMetric(label, value) {
  return `<div class="rail-metric"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`;
}

function renderArtifactRow(label, filename) {
  return `<div class="artifact-row"><span>${escapeHtml(label)}</span><em>${escapeHtml(filename)}</em></div>`;
}

function renderMessage(message) {
  const meta = roleMeta[message.role_id] || { color: "purple", title: "진행자", badge: "진행", avatar: "/static/avatar-moderator.svg" };
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
        <span class="message-route">전체 · ${escapeHtml(label)} · <span class="confidence">${escapeHtml(confidenceLabel(message.confidence))}</span></span>
      </div>
      ${position ? `<p class="stance-line"><strong>${escapeHtml(stance)}</strong> ${escapeHtml(position)}</p>` : ""}
      ${renderTextBlocks(message.content, { highlight: position })}
      </div>
    </article>
  `;
}

function displayNameLabel(event) {
  if (event.display_name) return event.display_name === "Moderator" ? "진행자" : event.display_name;
  if (event.role_id === "moderator") return "진행자";
  return "시스템";
}

function eventKindLabel(kind) {
  return {
    status: "상태",
    research: "리서치",
    synthesis: "종합",
    artifact: "산출물",
    message: "발언",
  }[kind] || "상태";
}

function roundKindLabel(round) {
  return roundLabel(state.payload?.meeting || {}, round, round);
}

function confidenceLabel(confidence) {
  return {
    low: "낮음",
    medium: "보통",
    high: "높음",
    unknown: "미정",
  }[confidence || "unknown"] || confidence || "미정";
}

function userVisibleSummary(text) {
  const value = String(text || "");
  if (hasInternalDiagnostics(value)) {
    return "구조화 응답이 완성되지 않아 보수적인 대체 결론을 사용했습니다. 진단 정보는 공식 발언이 아닌 내부 기록으로 분리됩니다.";
  }
  return value;
}

function hasInternalDiagnostics(text) {
  return [
    "Codex moderator synthesis did not return parseable JSON",
    "parseable JSON",
    "local fallback",
    "Local fallback decision",
    "Evidence Gate status",
    "turn/start failed",
    "Input exceeds",
  ].some((marker) => text.includes(marker));
}

function renderTextBlocks(text, options = {}) {
  return paragraphize(text)
    .map((line) => `<p>${highlightImportant(escapeHtml(line), options.highlight)}</p>`)
    .join("");
}

function paragraphize(text) {
  const normalized = String(text || "").replace(/\s+/g, " ").trim();
  if (!normalized) return [""];
  const sentences = normalized.match(/[^.!?。！？]+[.!?。！？]?/g) || [normalized];
  return sentences.flatMap((sentence) => splitLongSentence(sentence.trim())).filter(Boolean);
}

function splitLongSentence(sentence) {
  if (sentence.length <= 150) return [sentence];
  const chunks = [];
  let current = "";
  for (const part of sentence.split(/([,;:，；：、])/)) {
    const next = `${current}${part}`.trim();
    if (next.length > 110 && current) {
      chunks.push(current.trim());
      current = part.trim();
    } else {
      current = next;
    }
  }
  if (current) chunks.push(current.trim());
  return chunks.flatMap(splitOverlongText);
}

function splitOverlongText(text) {
  if (text.length <= 150) return [text];
  const chunks = [];
  for (let index = 0; index < text.length; index += 110) {
    chunks.push(text.slice(index, index + 110).trim());
  }
  return chunks.filter(Boolean);
}

function highlightImportant(html, needle) {
  const candidates = [needle, "입장 유지", "입장 변화", "반복된 입장", "근거 품질"].filter(Boolean);
  let highlighted = html;
  for (const candidate of candidates) {
    const escapedNeedle = escapeHtml(candidate);
    if (!escapedNeedle || !highlighted.includes(escapedNeedle)) continue;
    highlighted = highlighted.replaceAll(escapedNeedle, `<mark>${escapedNeedle}</mark>`);
  }
  return highlighted;
}

function isMeetingComplete(meeting) {
  return meeting?.live_status === "complete" || Boolean(meeting?.moderator_synthesis?.winner);
}

function stanceLabel(status) {
  if (status === "qualified") return "조건부 유지";
  if (status === "reframed") return "기준 재정의";
  if (status === "revised") return "일부 수정";
  if (status === "conceded") return "핵심 양보";
  if (status === "changed") return "입장 변화";
  if (status === "softened") return "입장 약화";
  if (status === "strengthened") return "입장 강화";
  if (status === "blocked") return "발언 실패";
  return "입장 유지";
}

export function renderBoard(payload) {
  const board = document.querySelector("#board");
  const meeting = payload.meeting;
  const gate = meeting.decision_gate || {};
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
          <span class="room-status">결정 ${escapeHtml(decisionGateLabel(gate.status))}</span>
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
        ${renderDecisionGateBoard(meeting)}
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

function renderDecisionGateBoard(meeting) {
  const gate = meeting.decision_gate || {};
  return `
    <section class="evidence-overview decision-gate-board">
      <div>
        <strong>결정 게이트</strong>
        <span class="status-pill status-${escapeHtml(gate.status || "unknown")}">${escapeHtml(decisionGateLabel(gate.status))}</span>
      </div>
      ${renderMetric("확정", gate.can_finalize ? "가능" : "보류")}
      ${renderMetric("조치", decisionActionLabel(gate.required_action))}
      ${renderMetric("사유", (gate.reasons || []).length)}
      ${renderMetric("소수", (gate.minority_positions || []).length)}
    </section>
  `;
}

function decisionGateLabel(status) {
  return {
    decided: "결정 가능",
    split_decision: "분리 결정",
    no_consensus: "합의 실패",
    needs_more_research: "추가 조사",
    blocked: "사람 판단",
    invalid: "무효",
    unknown: "미정",
  }[status || "unknown"] || status || "미정";
}

function decisionActionLabel(action) {
  return {
    write_decision: "결정 기록",
    record_split_decision: "반대 의견 포함",
    add_round_or_user_decision: "라운드 추가/사용자 판단",
    run_research_or_verifier_round: "재조사/검증",
    user_decision_or_add_round: "사용자 판단/라운드 추가",
    rerun_moderator_or_user_review: "모더레이터 재실행",
    rerun_failed_debate_round: "실패 발언 재실행",
  }[action || "unknown"] || action || "미정";
}

function buildStanceSummary(meeting) {
  const items = new Map();
  const fallbackPosition = meeting.moderator_synthesis?.winner || "입장 미정";
  for (const round of meeting.debate_rounds || []) {
    for (const message of round.messages || []) {
      const stance = messagePosition(message, meeting, fallbackPosition);
      if (!stance) continue;
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
  if (!message || message.status === "failed" || message.stance_status === "blocked") {
    return "";
  }
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
        <strong>${escapeHtml(messagePosition(latestMessage, payload.meeting) || "입장 미정")}</strong>
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
  if (!claims.length) return "";
  return `
    <details class="claim-group claim-${kind}">
      <summary>${escapeHtml(title)} · ${claims.length}</summary>
      <div class="claim-table-wrap">
        <table class="evidence-claims-table">
          <thead>
            <tr>
              <th>주장</th>
              <th>근거/사유</th>
              <th>출처</th>
            </tr>
          </thead>
          <tbody>${claims.map(renderClaimRow).join("")}</tbody>
        </table>
      </div>
    </details>
  `;
}

function renderClaimRow(claim) {
  const urls = claim.evidence || claim.sources || [];
  return `
    <tr>
      <td>${escapeHtml(claim.claim || "")}</td>
      <td>${escapeHtml(claim.reason || claim.interpretation || claim.why_it_matters || "")}</td>
      <td>${urls.length ? urls.map((url) => `<a href="${escapeHtml(url)}" target="_blank" rel="noreferrer">${escapeHtml(shortUrl(url))}</a>`).join(" ") : "출처 없음"}</td>
    </tr>
  `;
}

function shortUrl(url) {
  try {
    const parsed = new URL(url);
    return parsed.hostname.replace(/^www\./, "");
  } catch {
    return String(url || "");
  }
}
