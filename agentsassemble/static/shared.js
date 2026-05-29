export const state = {
  currentTab: "lobby",
  meetings: [],
  payload: null,
  archiveKey: "decision.md",
  lobbyEvents: [],
  lobbySignature: "[]",
  sideChatEvents: [],
  sideChatSignature: "[]",
  providerHealthRunning: false,
  providerHealthStatus: null,
  liveAgents: [],
  liveAgentsLoaded: false,
  liveAgentsLoading: false,
  liveAgentStatus: null,
  liveAgentJoinBrief: null,
  liveAgentJoinBriefRunning: false,
  liveAgentProbeRunning: "",
  liveAgentHealth: null,
  liveAgentHealthLoaded: false,
  liveAgentHealthLoading: false,
  liveAgentProcesses: [],
  liveAgentProcessesLoaded: false,
  liveAgentProcessesLoading: false,
  liveAgentProcessEvents: [],
  liveAgentProcessEventsLoaded: false,
  liveAgentProcessEventsLoading: false,
  liveAgentProcessEventsMeta: null,
  liveAgentOperations: [],
  liveAgentOperationsLoaded: false,
  liveAgentOperationsLoading: false,
  liveAgentSessionRuns: [],
  liveAgentSessionRunsLoaded: false,
  liveAgentSessionRunsLoading: false,
  liveAgentSessionRunActionRunning: "",
  liveAgentSessionRunRetryNowRunning: "",
  liveAgentFlow: null,
  liveAgentFlowEvents: [],
  liveAgentFlowLoaded: false,
  liveAgentFlowLoading: false,
  liveAgentFlowStartRunning: false,
  liveAgentFlowStopRunning: false,
  liveAgentProcessStartRunning: false,
  liveAgentSessionStartRunning: false,
  liveAgentSessionRestartRunning: false,
  liveAgentSessionRecoverRunning: false,
  liveAgentSessionCheckRunning: false,
  liveAgentSessionStopRunning: false,
  liveAgentReviewCheckpointRunning: false,
  liveAgentPreflightRunning: false,
  liveAgentSmokeRunning: false,
  liveAgentOfficialRoundSmokeRunning: false,
  liveAgentSessionSmokeRunning: false,
  liveAgentReadinessRunning: false,
  liveAgentProcessRowActionRunning: "",
  liveAgentProcessBulkStopRunning: false,
  liveAgentDiscoveryRunning: false,
  liveAgentAutoJoinRunning: false,
  liveAgentDiscoveryReport: null,
  liveAgentProcessStatus: null,
  liveAgentRoundCallRunning: false,
  codexSessions: [],
  codexSessionsLoaded: false,
  codexSessionsLoading: false,
  codexInviteStatus: null,
};

export const roleMeta = {
  lore_lawyer: { color: "red", title: "공식 설정 담당", badge: "설정/정합성", avatar: "/static/avatar-lore.svg" },
  show_me_the_feats: { color: "cyan", title: "전투 묘사 담당", badge: "전적/퍼포먼스", avatar: "/static/avatar-feats.svg" },
  fanboard_skeptic: { color: "green", title: "게시판식 반례 검증 담당", badge: "갤럼/반박", avatar: "/static/avatar-skeptic.svg" },
  animal_spec_nerd: { color: "red", title: "동물 스펙 분석 담당", badge: "동물/스펙", avatar: "/static/avatar-lore.svg" },
  gym_tactics_bro: { color: "cyan", title: "인간 전술 분석 담당", badge: "전술/숫자", avatar: "/static/avatar-feats.svg" },
  playground_skeptic: { color: "green", title: "운동장식 반례 검증 담당", badge: "룰/반박", avatar: "/static/avatar-skeptic.svg" },
};

export const lensLabels = {
  "Canon Analyst": "공식 설정 분석",
  "Feats Analyst": "전투 묘사 분석",
  "Skeptical Critic": "반례 검증",
  "Animal Biology Analyst": "동물 생물학 분석",
  "Human Tactics Analyst": "인간 전술 분석",
  "Playground Skeptic": "운동장식 반례 검증",
};

export const focusLabels = {
  lore_lawyer: "공식 언급, 설정 우선순위, 원작 정합성을 봅니다.",
  show_me_the_feats: "실제 전투 장면, 승패, 능력 사용 결과를 봅니다.",
  fanboard_skeptic: "팬덤 과장, 약한 근거, 반례와 불확실성을 커뮤니티식으로 세게 찌릅니다.",
  animal_spec_nerd: "고릴라의 실제 생물 스펙, 행동 패턴, 부상 리스크를 봅니다.",
  gym_tactics_bro: "100명이라는 숫자, 협동 전술, 체력과 사기 유지 가능성을 봅니다.",
  playground_skeptic: "룰 허점, 앞줄 공포, 동시에 달려드는 게 가능한지 같은 현실성을 찌릅니다.",
};

export const roundLabels = {
  round_1: "1라운드 · 첫 주장",
  round_2: "2라운드 · 반박/비교",
};

export function roundLabel(meeting, roundId, fallback) {
  const templateRound = (meeting.meeting_template?.rounds || []).find((round) => round.id === roundId);
  return roundLabels[roundId] || templateRound?.title || fallback || roundId;
}

export function displayTopic(meeting) {
  if (meeting.display_topic) return meeting.display_topic;
  if (meeting.topic === "One Piece admiral strength debate") return "원피스 3대장 최강자 토론";
  return meeting.topic || "회의";
}

export function displayQuestion(question) {
  const meeting = state.payload?.meeting;
  if (meeting?.display_question) return meeting.display_question;
  if (question === "Who is the strongest One Piece admiral?") return "원피스 3대장 중 누가 제일 센가?";
  return question || "";
}

export function mergeMeetingStreamSnapshotPayload(previousPayload, snapshot) {
  if (!previousPayload?.meeting || !snapshot?.meeting) return previousPayload;
  if (snapshot.meeting.meeting_id && snapshot.meeting.meeting_id !== previousPayload.meeting.meeting_id) {
    return previousPayload;
  }
  return {
    ...previousPayload,
    meeting: {
      ...previousPayload.meeting,
      ...snapshot.meeting,
    },
    lifecycle: snapshot.lifecycle ?? previousPayload.lifecycle,
    live_events: mergeEventsById(previousPayload.live_events || [], snapshot.live_events || []),
  };
}

export function meetingStatusLabel(status) {
  return {
    running: "진행 중",
    stalled: "중단됨",
    complete: "완료",
    failed: "실패",
    unknown: "상태 미정",
  }[status || "unknown"] || status;
}

export function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

const lifecycleCopy = {
  preparing: {
    stepLabel: "준비 중",
    nextAction: "회의 목표와 역할 바인딩을 확인하세요.",
  },
  waiting_for_agents: {
    stepLabel: "입장 대기",
    nextAction: "미입실 역할을 초대하거나 승인 상태를 확인하세요.",
  },
  running_official_turns: {
    stepLabel: "공식 진행",
    nextAction: "공식 발언과 공유 메모리 갱신을 확인하세요.",
  },
  blocked_by_pending_turns: {
    stepLabel: "응답 대기",
    nextAction: "대기 중인 공식 턴을 기다리거나 명시적으로 닫으세요.",
  },
  finalized: {
    stepLabel: "완료됨",
    nextAction: "아카이브에서 transcript, decision, shared memory를 확인하세요.",
  },
  stopped: {
    stepLabel: "정지됨",
    nextAction: "필요하면 명시적으로 재개하거나 종료 기록을 확인하세요.",
  },
  archived: {
    stepLabel: "기록만 있음",
    nextAction: "아카이브에서 최종 산출물과 리뷰 기록을 확인하세요.",
  },
  unknown: {
    stepLabel: "상태 불명",
    nextAction: "라이프사이클 기록을 확인하세요.",
  },
  none: {
    stepLabel: "회의 없음",
    nextAction: "로비에서 새 회의를 시작하거나 기존 회의를 선택하세요.",
  },
};

const lifecycleAttentionCopy = {
  pending_official_turns: "공식 턴 대기",
  stalled_running_state: "장시간 갱신 없음",
  malformed: "기록 파싱 오류",
};

const lifecycleStatusSourceCopy = {
  live_state: "실시간 상태",
  final_record: "최종 기록",
  stale_running_inference: "정지 추정",
  missing_state: "기록 없음",
  malformed_record: "손상된 기록",
};

export function summarizeLifecycleForStaticGui(lifecycle) {
  const hasLifecycle = lifecycle && typeof lifecycle === "object";
  const lifecycleState = hasLifecycle ? String(lifecycle.state || "unknown").trim() : "none";
  const copy = lifecycleCopy[lifecycleState] || lifecycleCopy.unknown;
  const roleHints = Array.isArray(lifecycle?.role_hints) ? lifecycle.role_hints : [];
  const counts = lifecycle?.counts && typeof lifecycle.counts === "object" ? lifecycle.counts : {};
  const rolesTotal = Math.max(nonNegativeNumber(counts.roles), roleHints.length);
  const boundRoles = roleHints.filter((role) => String(role?.admission_status || "") === "bound_to_meeting").length;
  const unsafePermissionViolations = roleHints.reduce(
    (total, role) => total + nonNegativeNumber(role?.unsafe_permission_violations),
    0
  );
  return {
    state: lifecycleCopy[lifecycleState] ? lifecycleState : "unknown",
    stepLabel: copy.stepLabel,
    nextAction: copy.nextAction,
    statusSourceLabel: lifecycleStatusSourceCopy[String(lifecycle?.status_source || "").trim()] || "기록 없음",
    rolesTotal,
    boundRoles,
    missingRoles: Math.max(0, rolesTotal - boundRoles),
    unsafePermissionViolations,
    liveAgents: nonNegativeNumber(counts.live_agents),
    pendingTurns: nonNegativeNumber(counts.pending_turns),
    officialMessages: nonNegativeNumber(counts.official_messages),
    attentionLabels: (Array.isArray(lifecycle?.attention) ? lifecycle.attention : [])
      .map((code) => lifecycleAttentionCopy[String(code || "").trim()] || String(code || "").trim())
      .filter(Boolean),
  };
}

export function renderLifecycleBanner(payload, options = {}) {
  const lifecycle = payload?.lifecycle || null;
  const summary = summarizeLifecycleForStaticGui(lifecycle);
  const surface = String(options.surface || "room").trim() || "room";
  const detailChips = lifecycle
    ? [
        `역할 ${summary.boundRoles}/${summary.rolesTotal}`,
        `상주 ${summary.liveAgents}`,
        summary.pendingTurns ? `대기 턴 ${summary.pendingTurns}` : "",
        summary.officialMessages ? `공식 ${summary.officialMessages}` : "",
      ].filter(Boolean)
    : ["회의 선택 필요"];
  const attention = summary.attentionLabels.length
    ? `<div class="meeting-lifecycle-attention" aria-label="주의">${summary.attentionLabels
        .map((label) => `<span>${escapeHtml(label)}</span>`)
        .join("")}</div>`
    : "";
  return `
    <section class="meeting-lifecycle-banner meeting-lifecycle-${escapeHtml(summary.state)}" data-lifecycle-surface="${escapeHtml(surface)}" aria-label="라이프사이클">
      <div class="meeting-lifecycle-copy">
        <span>${escapeHtml(summary.statusSourceLabel)}</span>
        <strong>${escapeHtml(summary.stepLabel)}</strong>
        <p>${escapeHtml(summary.nextAction)}</p>
      </div>
      <div class="meeting-lifecycle-meta">
        ${detailChips.map((label) => `<em>${escapeHtml(label)}</em>`).join("")}
        ${summary.unsafePermissionViolations ? `<em class="is-warning">권한 검토 ${escapeHtml(summary.unsafePermissionViolations)}</em>` : ""}
      </div>
      ${attention}
    </section>
  `;
}

function nonNegativeNumber(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return 0;
  return Math.max(0, Math.trunc(number));
}

export async function fetchJson(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) {
    const details = await responseErrorDetails(response);
    const error = new Error(details.message);
    error.payload = details.payload;
    error.status = response.status;
    throw error;
  }
  return response.json();
}

async function responseErrorMessage(response) {
  const details = await responseErrorDetails(response);
  return details.message;
}

async function responseErrorDetails(response) {
  const fallback = `Request failed: ${response.status}`;
  try {
    const contentType = response.headers.get("Content-Type") || "";
    if (contentType.includes("application/json")) {
      const payload = await response.json();
      return { message: String(payload?.error || payload?.message || fallback), payload };
    }
    const text = (await response.text()).trim();
    return { message: text || fallback, payload: null };
  } catch {
    return { message: fallback, payload: null };
  }
}

export function lobbyEventsSignature(events) {
  return JSON.stringify(events || []);
}

export function mergeEventById(events, event) {
  if (!event?.id) return events || [];
  const merged = [...(events || [])];
  const index = merged.findIndex((candidate) => candidate.id === event.id);
  if (index >= 0) merged[index] = { ...merged[index], ...event };
  else merged.push(event);
  return merged;
}

export function mergeEventsById(events, incoming) {
  return (incoming || []).reduce((merged, event) => mergeEventById(merged, event), events || []);
}

export function setLobbyEvents(events) {
  state.lobbyEvents = events || [];
  state.lobbySignature = lobbyEventsSignature(state.lobbyEvents);
}

export function setSideChatEvents(events) {
  state.sideChatEvents = events || [];
  state.sideChatSignature = lobbyEventsSignature(state.sideChatEvents);
}

export function setLiveAgents(agents) {
  state.liveAgents = agents || [];
}

export function setLiveAgentProcesses(groups) {
  state.liveAgentProcesses = groups || [];
}

export function setLiveAgentOperations(operations) {
  state.liveAgentOperations = operations || [];
}

export function setLiveAgentSessionRuns(runs) {
  state.liveAgentSessionRuns = runs || [];
}

export function bindingForRole(meeting, roleId) {
  return (meeting?.agent_bindings || []).find((binding) => binding.role_id === roleId) || null;
}

export function providerForBinding(meeting, binding) {
  if (!binding) return null;
  return meeting?.provider_configs?.[binding.provider_id] || null;
}

export function permissionsForBinding(meeting, binding) {
  if (!binding) return null;
  return meeting?.permission_profiles?.[binding.permission_profile_id] || null;
}

export function bindingSummary(meeting, roleId) {
  const binding = bindingForRole(meeting, roleId);
  const provider = providerForBinding(meeting, binding);
  const permissions = permissionsForBinding(meeting, binding);
  return { binding, provider, permissions };
}
