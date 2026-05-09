import { renderArchive } from "./archive.js";
import { renderLobby } from "./lobby.js";
import { renderBoard, renderLive } from "./meeting-views.js";
import { displayTopic, fetchJson, state } from "./shared.js";

const tabs = document.querySelectorAll(".tab");
const panels = document.querySelectorAll(".panel");
const emptyState = document.querySelector("#empty-state");
const runDemo = document.querySelector("#run-demo");
const meetingSelect = document.querySelector("#meeting-select");
const subtitle = document.querySelector("#meeting-subtitle");
const uiScale = document.querySelector("#ui-scale");
const textScale = document.querySelector("#text-scale");
const appStatus = document.querySelector("#app-status");

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
    option.textContent = `${meeting.meeting_id} · ${displayTopic(meeting)}`;
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
  renderLive(payload, { followLatest: options.liveRefresh && state.currentTab === "live" });
  renderBoard(payload);
  renderArchive(payload);
}

function payloadSignature(payload) {
  if (!payload?.meeting) return "empty";
  return JSON.stringify(payload);
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
  setInterval(refreshCurrentMeeting, 2000);
})();
