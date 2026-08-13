import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import {
  checkPermissions,
  Format,
  requestPermissions,
  scan,
} from "@tauri-apps/plugin-barcode-scanner";

const status = document.querySelector("#launcher-status");
const detail = document.querySelector("#startup-detail");
const elapsed = document.querySelector("#startup-elapsed");
const progress = document.querySelector(".startup-progress");
const retry = document.querySelector("#retry-startup");
const cachedRoomList = document.querySelector("#cached-room-list");
const cachedRoomCount = document.querySelector("#cached-room-count");
const clientPlatformLabel = document.querySelector("#client-platform-label");
const mobileConnect = document.querySelector("#mobile-connect");
const mobileConnectStatus = document.querySelector("#mobile-connect-status");
const serverLinkForm = document.querySelector("#server-link-form");
const serverLinkInput = document.querySelector("#server-link");
const scanRoomQr = document.querySelector("#scan-room-qr");
let startedAt = 0;
let elapsedTimer = 0;
let clientPlatform = "desktop";

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

async function openRoomLink(url) {
  const value = String(url || "").trim();
  if (!value) throw new Error("서버 주소나 초대·복구 링크를 입력해 주세요.");
  await invoke("open_server_link", { url: value });
}

function cachedRoomUrl(room) {
  if (!room.serverOrigin) return "";
  try {
    const url = new URL("/", room.serverOrigin);
    url.searchParams.set("room", String(room.meetingId || ""));
    url.searchParams.set("name", String(room.label || room.meetingId || "Room"));
    return url.toString();
  } catch {
    return "";
  }
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
    const roomUrl = cachedRoomUrl(room);
    const row = document.createElement(roomUrl ? "button" : "article");
    row.className = "cached-room";
    if (roomUrl) {
      row.type = "button";
      row.addEventListener("click", () => {
        mobileConnectStatus.textContent = `${String(room.label || room.meetingId)} 서버에 연결하는 중…`;
        void openRoomLink(roomUrl).catch((error) => {
          mobileConnectStatus.textContent = String(error?.message || error);
        });
      });
    }
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

async function initializeMobile() {
  clientPlatformLabel.textContent = "AGENTSASSEMBLE MOBILE";
  document.querySelector("#startup-title").textContent = "AgentsAssemble";
  document.querySelector(".lead").textContent =
    "저장된 룸을 다시 열거나 새 초대·복구 링크로 연결하세요.";
  progress.classList.add("hidden");
  mobileConnect.classList.remove("hidden");
  await loadCachedRooms();
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

serverLinkForm.addEventListener("submit", (event) => {
  event.preventDefault();
  mobileConnectStatus.textContent = "룸 서버에 연결하는 중…";
  void openRoomLink(serverLinkInput.value).catch((error) => {
    mobileConnectStatus.textContent = String(error?.message || error);
  });
});

scanRoomQr.addEventListener("click", () => {
  mobileConnectStatus.textContent = "카메라 권한을 확인하는 중…";
  void (async () => {
    let permission = await checkPermissions();
    if (permission !== "granted") permission = await requestPermissions();
    if (permission !== "granted") {
      throw new Error("QR 스캔을 사용하려면 카메라 권한이 필요합니다.");
    }
    mobileConnectStatus.textContent = "룸 QR 코드를 비춰 주세요.";
    const result = await scan({ formats: [Format.QRCode], windowed: false });
    serverLinkInput.value = result.content;
    mobileConnectStatus.textContent = "QR 링크를 확인했습니다. 룸을 여는 중…";
    await openRoomLink(result.content);
  })().catch((error) => {
    mobileConnectStatus.textContent = String(error?.message || error);
  });
});

async function initializeClient() {
  try {
    clientPlatform = await invoke("client_platform");
  } catch {
    clientPlatform = "desktop";
  }
  if (clientPlatform === "mobile") {
    await initializeMobile();
    return;
  }
  clientPlatformLabel.textContent = "AGENTSASSEMBLE DESKTOP";
  // Event ACL or plugin gaps must not block the local room engine.
  try {
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
  } catch {
    // Continue without update progress events.
  }
  void loadCachedRooms();
  await updateBeforeStartup();
}

void initializeClient().catch((error) => {
  showError(error);
});
