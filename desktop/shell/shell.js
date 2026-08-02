import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";

const status = document.querySelector("#launcher-status");
const detail = document.querySelector("#startup-detail");
const elapsed = document.querySelector("#startup-elapsed");
const progress = document.querySelector(".startup-progress");
const retry = document.querySelector("#retry-startup");
const cachedRoomList = document.querySelector("#cached-room-list");
const cachedRoomCount = document.querySelector("#cached-room-count");
let startedAt = 0;
let elapsedTimer = 0;

function showProgress(message) {
  retry.classList.add("hidden");
  progress.classList.remove("error");
  status.textContent = message;
  detail.textContent = "앱은 멈춘 것이 아닙니다. 준비가 끝나면 룸 목록으로 자동 이동합니다.";
}

function showUpdateProgress(message, downloaded = 0, total = 0) {
  retry.classList.add("hidden");
  progress.classList.remove("error");
  status.textContent = message;
  if (total > 0) {
    const percent = Math.min(100, Math.floor((downloaded / total) * 100));
    detail.textContent = `${percent}% · ${downloaded.toLocaleString()} / ${total.toLocaleString()} bytes`;
  } else {
    detail.textContent = "서명된 업데이트를 안전하게 내려받고 있습니다.";
  }
}

function showError(error) {
  window.clearInterval(elapsedTimer);
  progress.classList.add("error");
  status.textContent = "로컬 클라이언트를 시작하지 못했습니다.";
  detail.textContent = String(error?.message || error || "알 수 없는 시작 오류입니다.");
  retry.classList.remove("hidden");
}

async function openServer(server) {
  await invoke("open_server", { server });
}

function renderCachedRooms(rooms) {
  cachedRoomList.replaceChildren();
  cachedRoomCount.textContent = `${rooms.length}개`;
  if (!rooms.length) {
    const empty = document.createElement("p");
    empty.className = "cached-room-empty";
    empty.textContent = "아직 이 컴퓨터에 저장된 룸이 없습니다.";
    cachedRoomList.append(empty);
    return;
  }
  rooms.forEach((room) => {
    const row = document.createElement("article");
    row.className = "cached-room";
    const icon = document.createElement("span");
    icon.className = "cached-room-icon";
    icon.textContent = String(room.shortLabel || room.label || "R").slice(0, 1).toUpperCase();
    const text = document.createElement("div");
    const title = document.createElement("strong");
    title.textContent = String(room.label || room.meetingId || "저장된 룸");
    const state = document.createElement("span");
    const remote = room.roomOrigin === "remote_server";
    state.textContent = remote ? "연결이 끊긴 서버" : "로컬 룸";
    state.dataset.state = remote ? "disconnected" : "local";
    text.append(title, state);
    row.append(icon, text);
    cachedRoomList.append(row);
  });
}

async function loadCachedRooms() {
  try {
    const raw = await invoke("load_cached_room_directory");
    const rooms = JSON.parse(raw);
    renderCachedRooms(Array.isArray(rooms) ? rooms : []);
  } catch {
    cachedRoomCount.textContent = "확인 불가";
    cachedRoomList.querySelector(".cached-room-empty").textContent =
      "저장된 룸 기록을 읽지 못했습니다. 로컬 엔진은 계속 시작합니다.";
  }
}

async function startClient() {
  startedAt = Date.now();
  elapsed.textContent = "0초";
  window.clearInterval(elapsedTimer);
  elapsedTimer = window.setInterval(() => {
    elapsed.textContent = `${Math.floor((Date.now() - startedAt) / 1000)}초`;
  }, 1000);
  showProgress("로컬 룸 엔진을 시작하는 중…");
  try {
    const server = await invoke("start_local_runtime");
    showProgress("저장된 룸을 불러오는 중…");
    await openServer(server);
  } catch (error) {
    showError(error);
  }
}

async function updateBeforeStartup() {
  showProgress("앱 업데이트를 확인하는 중…");
  try {
    const result = await invoke("check_desktop_update");
    if (result?.state !== "available") {
      await startClient();
      return;
    }
    showUpdateProgress(`${result.version} 업데이트를 준비하는 중…`);
    await invoke("install_desktop_update");
  } catch (error) {
    detail.textContent = `업데이트 확인을 건너뜁니다: ${String(error?.message || error)}`;
    await new Promise((resolve) => window.setTimeout(resolve, 1200));
    await startClient();
  }
}

retry.addEventListener("click", startClient);

async function initializeDesktop() {
  await listen("desktop-update-progress", (event) => {
    const payload = event.payload || {};
    if (payload.phase === "finished") {
      showUpdateProgress("업데이트 설치를 마쳤습니다. 다시 시작하는 중…", 1, 1);
      return;
    }
    showUpdateProgress(
      "앱 업데이트를 내려받는 중…",
      Number(payload.downloaded || 0),
      Number(payload.total || 0)
    );
  });
  void loadCachedRooms();
  await updateBeforeStartup();
}

void initializeDesktop();
