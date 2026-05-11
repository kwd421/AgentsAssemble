export const state = {
  currentTab: "lobby",
  meetings: [],
  payload: null,
  archiveKey: "decision.md",
  lobbyEvents: [],
  lobbySignature: "[]",
  sideChatEvents: [],
  sideChatSignature: "[]",
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

export async function fetchJson(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) throw new Error(`Request failed: ${response.status}`);
  return response.json();
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
