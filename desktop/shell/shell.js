import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import {
  checkPermissions,
  Format,
  requestPermissions,
  scan,
} from "@tauri-apps/plugin-barcode-scanner";

import "./central.css";
import {
  bootstrapCentral,
  CentralAuthenticationError,
  clearCentralSession,
  configureCentralDirectory,
  createCentralGuest,
  fetchCentralConfig,
  loadCentralServers,
  loadCentralSession,
  loginCentralGoogle,
  logoutCentral,
  recoverCentralGuest,
  verifyKnownServer,
} from "./central-identity.js";

const status = document.querySelector("#launcher-status");
const detail = document.querySelector("#startup-detail");
const elapsed = document.querySelector("#startup-elapsed");
const progress = document.querySelector(".startup-progress");
const retry = document.querySelector("#retry-startup");
const cachedRooms = document.querySelector("#cached-rooms");
const cachedRoomList = document.querySelector("#cached-room-list");
const cachedRoomCount = document.querySelector("#cached-room-count");
const clientPlatformLabel = document.querySelector("#client-platform-label");
const startupNote = document.querySelector("#startup-note");
const mobileConnect = document.querySelector("#mobile-connect");
const mobileConnectStatus = document.querySelector("#mobile-connect-status");
const serverLinkForm = document.querySelector("#server-link-form");
const serverLinkInput = document.querySelector("#server-link");
const scanRoomQr = document.querySelector("#scan-room-qr");

const centralIdentity = document.querySelector("#central-identity");
const centralState = document.querySelector("#central-state");
const centralLoading = document.querySelector("#central-loading");
const centralLoadingText = document.querySelector("#central-loading-text");
const centralLogin = document.querySelector("#central-login");
const centralGoogle = document.querySelector("#central-google");
const centralNewGuest = document.querySelector("#central-new-guest");
const centralExistingGuest = document.querySelector("#central-existing-guest");
const centralGuestForm = document.querySelector("#central-guest-form");
const centralGuestName = document.querySelector("#central-guest-name");
const centralRecoverForm = document.querySelector("#central-recover-form");
const centralRecoveryInput = document.querySelector("#central-recovery-input");
const centralOffline = document.querySelector("#central-offline");
const centralRecovery = document.querySelector("#central-recovery");
const centralIssuedCode = document.querySelector("#central-issued-code");
const centralCopyCode = document.querySelector("#central-copy-code");
const centralCodeSaved = document.querySelector("#central-code-saved");
const centralRecoveryContinue = document.querySelector("#central-recovery-continue");
const centralHome = document.querySelector("#central-home");
const centralPersonName = document.querySelector("#central-person-name");
const centralPersonKind = document.querySelector("#central-person-kind");
const centralRefresh = document.querySelector("#central-refresh");
const centralLogout = document.querySelector("#central-logout");
const centralServerList = document.querySelector("#central-server-list");
const centralMessage = document.querySelector("#central-message");

let startedAt = 0;
let elapsedTimer = 0;
let clientPlatform = "desktop";
let cachedRoomsLoaded = false;
let centralBusy = false;
let googleEnabled = true;

function showProgress(message) {
  retry.classList.add("hidden");
  progress.classList.remove("error");
  status.textContent = message;
  detail.textContent =
    "앱은 멈춘 것이 아닙니다. 준비가 끝나면 로그인 또는 룸 화면으로 자동 이동합니다.";
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
    empty.textContent = "아직 이 기기에 저장된 룸이 없습니다.";
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
        mobileConnectStatus.textContent = `${String(
          room.label || room.meetingId
        )} 서버에 연결하는 중…`;
        void openRoomLink(roomUrl).catch((error) => {
          mobileConnectStatus.textContent = String(error?.message || error);
        });
      });
    }
    const icon = document.createElement("span");
    icon.className = "cached-room-icon";
    icon.textContent = String(room.shortLabel || room.label || "R")
      .slice(0, 1)
      .toUpperCase();
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
  if (cachedRoomsLoaded) return;
  cachedRoomsLoaded = true;
  try {
    const raw = await invoke("load_cached_room_directory");
    const rooms = JSON.parse(raw);
    renderCachedRooms(Array.isArray(rooms) ? rooms : []);
  } catch {
    cachedRoomCount.textContent = "확인 불가";
    const empty = cachedRoomList.querySelector(".cached-room-empty");
    if (empty) empty.textContent = "저장된 룸 기록을 읽지 못했습니다.";
  }
}

function revealMobileTools() {
  cachedRooms.classList.remove("hidden");
  mobileConnect.classList.remove("hidden");
  void loadCachedRooms();
}

function setCentralMessage(message = "", kind = "") {
  centralMessage.textContent = message;
  if (kind) centralMessage.dataset.kind = kind;
  else delete centralMessage.dataset.kind;
}

function showCentralPanel(panel) {
  centralLoading.classList.toggle("hidden", panel !== "loading");
  centralLogin.classList.toggle("hidden", panel !== "login");
  centralRecovery.classList.toggle("hidden", panel !== "recovery");
  centralHome.classList.toggle("hidden", panel !== "home");
}

function showCentralLoading(message) {
  showCentralPanel("loading");
  centralLoadingText.textContent = message;
  centralState.textContent = "확인 중";
  setCentralMessage();
}

function resetCentralLoginForms() {
  centralGuestForm.classList.add("hidden");
  centralRecoverForm.classList.add("hidden");
  centralGuestName.value = "";
  centralRecoveryInput.value = "";
}

function showCentralLogin(message = "") {
  showCentralPanel("login");
  resetCentralLoginForms();
  centralState.textContent = "로그인 필요";
  centralGoogle.disabled = !googleEnabled;
  setCentralMessage(
    message || (googleEnabled ? "" : "Google 로그인이 아직 중앙 Worker에 설정되지 않았습니다."),
    message ? "error" : ""
  );
}

function showRecoveryCode(code) {
  showCentralPanel("recovery");
  centralState.textContent = "코드 보관 필요";
  centralIssuedCode.value = String(code || "");
  centralCodeSaved.checked = false;
  centralRecoveryContinue.disabled = true;
  setCentralMessage();
  centralIssuedCode.focus();
  centralIssuedCode.select();
}

function serverState(server, stale) {
  if (!server?.endpoint?.origin) return { label: "주소 없음", state: "offline" };
  if (stale) return { label: "캐시된 주소", state: "offline" };
  if (server.endpoint.status === "likely_online") {
    return { label: "최근 온라인", state: "online" };
  }
  return { label: "오프라인 가능", state: "offline" };
}

function renderCentralServers(servers, { stale = false } = {}) {
  centralServerList.replaceChildren();
  if (!servers.length) {
    const empty = document.createElement("p");
    empty.className = "central-server-empty";
    empty.textContent =
      "아직 중앙 목록에 서버가 없습니다. Mac에서 같은 신원으로 로그인하고 공개 호스팅을 켜면 여기에 나타납니다.";
    centralServerList.append(empty);
    return;
  }

  for (const server of servers) {
    const row = document.createElement("button");
    row.type = "button";
    row.className = "central-server";
    row.disabled = !server?.endpoint?.origin;

    const icon = document.createElement("span");
    icon.className = "central-server-icon";
    icon.textContent = String(server.alias || "S").slice(0, 1).toUpperCase();

    const main = document.createElement("span");
    main.className = "central-server-main";
    const title = document.createElement("strong");
    title.textContent = String(server.alias || server.server_id || "알려진 서버");
    const description = document.createElement("span");
    description.textContent =
      server.relation === "owner"
        ? "내 서버 · 연결 전 host key 확인"
        : "초대받은 서버 · 연결 전 host key 확인";
    main.append(title, description);

    const state = document.createElement("span");
    const currentState = serverState(server, stale);
    state.className = "central-server-state";
    state.dataset.state = currentState.state;
    state.textContent = currentState.label;

    row.append(icon, main, state);
    row.addEventListener("click", () => {
      if (centralBusy) return;
      centralBusy = true;
      row.disabled = true;
      setCentralMessage("서버에 직접 challenge를 보내 host key를 확인하는 중…");
      void verifyKnownServer(server)
        .then(async (origin) => {
          setCentralMessage("서버 신원을 확인했습니다. 여는 중…", "success");
          await openServer(origin);
        })
        .catch((error) => {
          setCentralMessage(String(error?.message || error), "error");
        })
        .finally(() => {
          centralBusy = false;
          row.disabled = !server?.endpoint?.origin;
        });
    });
    centralServerList.append(row);
  }
}

function showCentralHome(payload, { stale = false } = {}) {
  const session = loadCentralSession();
  const person = payload?.person || session?.person || {};
  const servers = payload?.servers || loadCentralServers();
  showCentralPanel("home");
  centralState.textContent = stale ? "캐시 사용 중" : "동기화됨";
  centralPersonName.textContent = String(person.display_name || "사용자");
  centralPersonKind.textContent =
    person.identity_kind === "google" ? "Google 중앙 신원" : "복구 가능한 게스트";
  renderCentralServers(servers, { stale });
  revealMobileTools();
}

async function refreshCentralHome() {
  if (centralBusy) return;
  centralBusy = true;
  centralRefresh.disabled = true;
  showCentralLoading("내 서버 목록을 새로 불러오는 중…");
  try {
    const payload = await bootstrapCentral();
    if (!payload) {
      showCentralLogin("중앙 로그인이 없습니다. 다시 로그인해 주세요.");
      return;
    }
    showCentralHome(payload);
    setCentralMessage("서버 목록을 새로 불러왔습니다.", "success");
  } catch (error) {
    if (error instanceof CentralAuthenticationError) {
      showCentralLogin("중앙 로그인이 만료됐습니다. 다시 로그인해 주세요.");
      return;
    }
    const cached = loadCentralServers();
    showCentralHome(null, { stale: true });
    setCentralMessage(
      cached.length
        ? "중앙에 연결하지 못해 마지막 서버 목록을 표시합니다."
        : String(error?.message || error),
      "error"
    );
  } finally {
    centralBusy = false;
    centralRefresh.disabled = false;
  }
}

async function initializeCentralIdentity() {
  centralIdentity.classList.remove("hidden");
  showCentralLoading("중앙 디렉터리 설정을 확인하는 중…");
  try {
    configureCentralDirectory(await invoke("central_directory_url"));
  } catch (error) {
    showCentralLogin(String(error?.message || error));
    centralOffline.classList.remove("hidden");
    return;
  }

  try {
    const config = await fetchCentralConfig();
    googleEnabled = Boolean(config.google_enabled);
  } catch {
    googleEnabled = true;
  }

  const session = loadCentralSession();
  if (!session) {
    showCentralLogin();
    return;
  }

  showCentralLoading("중앙 신원과 서버 목록을 확인하는 중…");
  try {
    const payload = await bootstrapCentral();
    if (!payload) {
      showCentralLogin();
      return;
    }
    showCentralHome(payload);
  } catch (error) {
    if (error instanceof CentralAuthenticationError) {
      showCentralLogin("중앙 로그인이 만료됐습니다. 다시 로그인해 주세요.");
      return;
    }
    showCentralHome(null, { stale: true });
    setCentralMessage(
      "중앙에 연결하지 못해 마지막 서버 목록과 저장된 룸을 표시합니다.",
      "error"
    );
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
    showProgress("로그인과 룸 목록을 준비하는 중…");
    await openServer(server);
  } catch (error) {
    showError(error);
  }
}

async function initializeMobile() {
  clientPlatformLabel.textContent = "AGENTSASSEMBLE MOBILE";
  document.querySelector("#startup-title").textContent = "AgentsAssemble";
  document.querySelector(".lead").textContent =
    "먼저 중앙 신원으로 로그인한 뒤 내 서버를 안전하게 선택하세요.";
  progress.classList.add("hidden");
  startupNote.textContent =
    "중앙에는 신원과 서버 주소 목록만 저장됩니다. 메시지·첨부·방 권한은 선택한 컴퓨터의 엔진에 남습니다.";
  await initializeCentralIdentity();
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
    detail.textContent = `업데이트 확인을 건너뜁니다: ${String(
      error?.message || error
    )}`;
    await new Promise((resolve) => window.setTimeout(resolve, 1200));
    await startClient();
  }
}

retry.addEventListener("click", startClient);

centralNewGuest.addEventListener("click", () => {
  resetCentralLoginForms();
  centralGuestForm.classList.remove("hidden");
  centralGuestName.focus();
  setCentralMessage();
});

centralExistingGuest.addEventListener("click", () => {
  resetCentralLoginForms();
  centralRecoverForm.classList.remove("hidden");
  centralRecoveryInput.focus();
  setCentralMessage();
});

document.querySelectorAll("[data-central-back]").forEach((button) => {
  button.addEventListener("click", () => {
    resetCentralLoginForms();
    setCentralMessage();
  });
});

centralGuestForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const displayName = centralGuestName.value.trim();
  if (!displayName || centralBusy) return;
  centralBusy = true;
  showCentralLoading("복구 가능한 게스트 신원을 만드는 중…");
  void createCentralGuest(displayName)
    .then((result) => showRecoveryCode(result.recovery_code))
    .catch((error) => showCentralLogin(String(error?.message || error)))
    .finally(() => {
      centralBusy = false;
    });
});

centralRecoverForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const recoveryCode = centralRecoveryInput.value.trim();
  if (!recoveryCode || centralBusy) return;
  centralBusy = true;
  showCentralLoading("게스트 신원을 복구하고 이전 코드를 폐기하는 중…");
  void recoverCentralGuest(recoveryCode)
    .then((result) => showRecoveryCode(result.recovery_code))
    .catch((error) => showCentralLogin(String(error?.message || error)))
    .finally(() => {
      centralBusy = false;
    });
});

centralGoogle.addEventListener("click", () => {
  if (centralBusy || !googleEnabled) return;
  centralBusy = true;
  showCentralLoading("Google 로그인을 준비하는 중…");
  void loginCentralGoogle(
    (url) => invoke("open_central_google_login", { url }),
    (message) => {
      centralLoadingText.textContent = message;
    }
  )
    .then(() => refreshCentralHome())
    .catch((error) => showCentralLogin(String(error?.message || error)))
    .finally(() => {
      centralBusy = false;
    });
});

centralCopyCode.addEventListener("click", () => {
  void navigator.clipboard
    .writeText(centralIssuedCode.value)
    .then(() => setCentralMessage("복구 코드를 복사했습니다.", "success"))
    .catch(() => {
      centralIssuedCode.focus();
      centralIssuedCode.select();
      setCentralMessage("자동 복사가 거부되어 코드를 선택했습니다.", "error");
    });
});

centralCodeSaved.addEventListener("change", () => {
  centralRecoveryContinue.disabled = !centralCodeSaved.checked;
});

centralRecoveryContinue.addEventListener("click", () => {
  if (!centralCodeSaved.checked || centralBusy) return;
  void refreshCentralHome();
});

centralRefresh.addEventListener("click", () => {
  void refreshCentralHome();
});

centralLogout.addEventListener("click", () => {
  if (centralBusy) return;
  centralBusy = true;
  showCentralLoading("중앙 세션을 종료하는 중…");
  void logoutCentral()
    .catch(() => clearCentralSession())
    .finally(() => {
      centralBusy = false;
      cachedRooms.classList.add("hidden");
      mobileConnect.classList.add("hidden");
      showCentralLogin();
    });
});

centralOffline.addEventListener("click", () => {
  revealMobileTools();
  centralState.textContent = "오프라인 모드";
  setCentralMessage(
    "중앙 로그인을 건너뛰었습니다. 저장된 룸과 직접 받은 링크만 사용할 수 있습니다."
  );
});

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
  centralIdentity.classList.add("hidden");
  cachedRooms.classList.add("hidden");
  startupNote.textContent =
    "준비가 끝나기 전에는 이전 룸 화면을 표시하지 않습니다.";
  try {
    await listen("desktop-update-progress", (event) => {
      const payload = event.payload || {};
      if (payload.phase === "finished") {
        showUpdateProgress(
          "업데이트 설치를 마쳤습니다. 다시 시작하는 중…",
          1,
          1
        );
        return;
      }
      showUpdateProgress(
        "앱 업데이트를 내려받는 중…",
        Number(payload.downloaded || 0),
        Number(payload.total || 0)
      );
    });
  } catch {
    // Event ACL or plugin gaps must not block the local room engine.
  }
  await updateBeforeStartup();
}

void initializeClient().catch((error) => {
  showError(error);
});
