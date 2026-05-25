import { renderArchive } from "./archive.js";
import { refreshLiveAgentRuntimeSurfaces, refreshLobbyFeed, renderLobby } from "./lobby.js";
import { refreshLiveTranscript, refreshSideChatFeed, renderBoard, renderLive } from "./meeting-views.js";
import {
  displayTopic,
  fetchJson,
  lobbyEventsSignature,
  meetingStatusLabel,
  mergeEventsById,
  setLobbyEvents,
  setSideChatEvents,
  state,
} from "./shared.js";

const tabs = document.querySelectorAll(".tab");
const panels = document.querySelectorAll(".panel");
const emptyState = document.querySelector("#empty-state");
const runDemo = document.querySelector("#run-demo");
const meetingSelect = document.querySelector("#meeting-select");
const subtitle = document.querySelector("#meeting-subtitle");
const uiScale = document.querySelector("#ui-scale");
const textScale = document.querySelector("#text-scale");
const appStatus = document.querySelector("#app-status");
const roomStreams = {
  lobby: null,
  sideChat: null,
  meeting: null,
  fallbackStarted: false,
  liveAgentRuntimeStarted: false,
  reconnectNotice: null,
};

function applyScaleSettings() {
  const ui = localStorage.getItem("agentsassemble.uiScale") || "90";
  const text = localStorage.getItem("agentsassemble.textScale") || "90";
  document.documentElement.style.setProperty("--ui-scale", String(Number(ui) / 100));
  document.documentElement.style.setProperty("--text-scale", String(Number(text) / 100));
  if (uiScale) uiScale.value = ui;
  if (textScale) textScale.value = text;
}

function showAppStatus(message, tone = "info") {
  if (!appStatus) return;
  appStatus.textContent = message;
  appStatus.dataset.tone = tone;
  appStatus.hidden = !message;
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
    option.textContent = `${meeting.meeting_id} · ${displayTopic(meeting)} · ${meetingStatusLabel(meeting.live_status)}`;
    meetingSelect.append(option);
  }
  return state.meetings[0].meeting_id;
}

async function loadMeeting(meetingId) {
  const url = meetingId ? `/api/meetings/${encodeURIComponent(meetingId)}` : "/api/meetings/latest";
  const payload = await fetchJson(url);
  state.payload = payload.meeting === null ? null : payload;
  state.payloadSignature = payloadSignature(state.payload);
  render();
  connectMeetingEventStream(state.payload?.meeting?.meeting_id);
}

async function refreshCurrentMeeting() {
  if (!state.payload?.meeting) return;
  const meetingId = state.payload.meeting.meeting_id;
  const payload = await fetchJson(`/api/meetings/${encodeURIComponent(meetingId)}`);
  const signature = payloadSignature(payload.meeting === null ? null : payload);
  if (signature === state.payloadSignature) return;
  state.payload = payload.meeting === null ? null : payload;
  state.payloadSignature = signature;
  render({ liveRefresh: true });
}

async function refreshCurrentMeetingSafely() {
  try {
    await refreshCurrentMeeting();
    showAppStatus("", "info");
  } catch {
    showAppStatus("실시간 갱신 대기 중", "info");
  }
}

async function loadLobby(options = {}) {
  const payload = await fetchJson("/api/lobby");
  const events = payload.events || [];
  const signature = lobbyEventsSignature(events);
  if (options.onlyIfChanged && signature === state.lobbySignature) return;
  setLobbyEvents(events);
  refreshLobbyFeed();
}

async function loadLobbySafely() {
  try {
    await loadLobby({ onlyIfChanged: true });
  } catch {
    showAppStatus("로비 갱신 대기 중", "info");
  }
}

async function loadSideChat(options = {}) {
  const payload = await fetchJson("/api/side-chat");
  const events = payload.events || [];
  const signature = lobbyEventsSignature(events);
  if (options.onlyIfChanged && signature === state.sideChatSignature) return;
  setSideChatEvents(events);
  if (state.payload?.meeting) refreshSideChatFeed();
}

async function loadSideChatSafely() {
  try {
    await loadSideChat({ onlyIfChanged: true });
  } catch {
    showAppStatus("비공식 채팅 갱신 대기 중", "info");
  }
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

function render(options = {}) {
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
  renderLive(payload, { followLatest: options.followLatest && state.currentTab === "live" });
  renderBoard(payload);
  renderArchive(payload);
}

function payloadSignature(payload) {
  if (!payload?.meeting) return "empty";
  return JSON.stringify(payload);
}

function connectRoomStreams() {
  if (!window.EventSource) {
    startPollingFallback();
    return;
  }
  connectLobbyEventStream();
  connectSideChatEventStream();
  connectMeetingEventStream(state.payload?.meeting?.meeting_id);
}

function connectLobbyEventStream() {
  roomStreams.lobby?.close();
  roomStreams.lobby = new EventSource("/api/events/lobby");
  roomStreams.lobby.onopen = clearReconnectNotice;
  roomStreams.lobby.addEventListener("lobby", (event) => applyLobbyStreamPayload(parseStreamPayload(event)));
  roomStreams.lobby.onerror = () => scheduleReconnectNotice("로비 스트림 재연결 중");
}

function connectSideChatEventStream() {
  roomStreams.sideChat?.close();
  roomStreams.sideChat = new EventSource("/api/events/side-chat");
  roomStreams.sideChat.onopen = clearReconnectNotice;
  roomStreams.sideChat.addEventListener("side_chat", (event) => applySideChatStreamPayload(parseStreamPayload(event)));
  roomStreams.sideChat.onerror = () => scheduleReconnectNotice("비공식 채팅 스트림 재연결 중");
}

function connectMeetingEventStream(meetingId) {
  roomStreams.meeting?.close();
  roomStreams.meeting = null;
  if (!window.EventSource || !meetingId) return;
  roomStreams.meeting = new EventSource(`/api/meetings/${encodeURIComponent(meetingId)}/events`);
  roomStreams.meeting.onopen = clearReconnectNotice;
  roomStreams.meeting.addEventListener("meeting", (event) => applyMeetingStreamPayload(parseStreamPayload(event)));
  roomStreams.meeting.onerror = () => scheduleReconnectNotice("회의 스트림 재연결 중");
}

function scheduleReconnectNotice(message) {
  clearTimeout(roomStreams.reconnectNotice);
  roomStreams.reconnectNotice = setTimeout(() => showAppStatus(message, "info"), 1800);
}

function clearReconnectNotice() {
  clearTimeout(roomStreams.reconnectNotice);
  roomStreams.reconnectNotice = null;
  showAppStatus("", "info");
}

function parseStreamPayload(event) {
  try {
    return JSON.parse(event.data || "{}");
  } catch {
    return {};
  }
}

function applyLobbyStreamPayload(payload) {
  const events = payload?.events || [];
  showAppStatus("", "info");
  if (!events.length) return;
  setLobbyEvents(mergeEventsById(state.lobbyEvents, events));
  refreshLobbyFeed();
}

function applySideChatStreamPayload(payload) {
  const events = payload?.events || [];
  showAppStatus("", "info");
  if (!events.length) return;
  setSideChatEvents(mergeEventsById(state.sideChatEvents, events));
  if (state.payload?.meeting) refreshSideChatFeed();
}

function applyMeetingStreamPayload(payload) {
  if (!state.payload?.meeting) return;
  if (payload.meeting_id && payload.meeting_id !== state.payload.meeting.meeting_id) return;
  showAppStatus("", "info");
  if (payload.meeting_payload?.meeting) {
    applyFullMeetingPayloadFromStream(payload.meeting_payload);
    return;
  }
  const events = payload?.events || [];
  if (!events.length) return;
  state.payload.live_events = mergeEventsById(state.payload.live_events || [], events);
  state.payloadSignature = payloadSignature(state.payload);
  refreshLiveTranscript(state.payload);
}

function applyFullMeetingPayloadFromStream(payload) {
  if (!payload?.meeting || payload.meeting.meeting_id !== state.payload?.meeting?.meeting_id) return;
  state.payload = payload;
  state.payloadSignature = payloadSignature(state.payload);
  render({ liveRefresh: true });
}

function startPollingFallback() {
  if (roomStreams.fallbackStarted) return;
  roomStreams.fallbackStarted = true;
  setInterval(loadLobbySafely, 4000);
  setInterval(loadSideChatSafely, 4000);
  setInterval(refreshCurrentMeetingSafely, 2000);
}

function startLiveAgentRuntimePolling() {
  if (roomStreams.liveAgentRuntimeStarted) return;
  roomStreams.liveAgentRuntimeStarted = true;
  setInterval(refreshLiveAgentRuntimeSurfaces, 5000);
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

window.addEventListener("agentsassemble:meeting-started", async (event) => {
  const meetingId = String(event.detail?.meetingId || "");
  if (!meetingId) return;
  try {
    await selectAndLoadMeeting(meetingId);
    showAppStatus("상주 세션 회의 연결됨", "success");
  } catch {
    showAppStatus("상주 세션 회의 갱신 대기 중", "info");
  }
});

window.addEventListener("agentsassemble:meeting-refresh-requested", async (event) => {
  const meetingId = String(event.detail?.meetingId || "");
  if (!meetingId) return;
  try {
    await selectAndLoadMeeting(meetingId);
    showAppStatus("회의 갱신 완료", "success");
  } catch {
    showAppStatus("회의 갱신 대기 중", "info");
  }
});

window.addEventListener("agentsassemble:live-agent-flow-updated", () => {
  if (!state.payload?.meeting || state.currentTab !== "live") return;
  renderLive(state.payload, { followLatest: false });
});

async function selectAndLoadMeeting(meetingId) {
  await loadMeetings();
  meetingSelect.value = meetingId;
  await loadMeeting(meetingId);
}

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
  await loadSideChat();
  const latest = await loadMeetings();
  await loadMeeting(latest);
  connectRoomStreams();
  startLiveAgentRuntimePolling();
})();
