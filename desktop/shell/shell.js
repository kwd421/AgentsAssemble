import { invoke } from "@tauri-apps/api/core";

const status = document.querySelector("#launcher-status");
const cloudForm = document.querySelector("#cloud-form");
const cloudInput = document.querySelector("#server-address");
const localButtons = [...document.querySelectorAll("[data-local-mode]")];
const allActions = [...localButtons, cloudForm.querySelector("button")];

function setBusy(message) {
  allActions.forEach((button) => {
    button.disabled = true;
  });
  status.className = "busy";
  status.textContent = message;
}

function showError(error) {
  allActions.forEach((button) => {
    button.disabled = false;
  });
  status.className = "error";
  status.textContent = String(error?.message || error || "연결하지 못했습니다.");
}

async function openServer(server) {
  await invoke("open_server", { server });
}

async function startLocal(mode) {
  setBusy(mode === "host" ? "이 컴퓨터에서 방을 준비하는 중…" : "로컬 공간을 준비하는 중…");
  try {
    const server = await invoke("start_local_runtime");
    await openServer(server);
  } catch (error) {
    showError(error);
  }
}

async function connectCloud(server) {
  setBusy("클라우드 서버를 확인하는 중…");
  try {
    const url = new URL(server);
    if (!['http:', 'https:'].includes(url.protocol)) {
      throw new Error("HTTP 또는 HTTPS 서버 주소를 입력하세요.");
    }
    await fetch(url.origin, { cache: "no-store", mode: "no-cors" });
    await openServer(url.origin);
  } catch (error) {
    showError(error);
  }
}

localButtons.forEach((button) => {
  button.addEventListener("click", () => startLocal(button.dataset.localMode));
});

cloudForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const server = cloudInput.value.trim();
  if (!server) {
    showError("클라우드 서버 주소를 입력하세요.");
    cloudInput.focus();
    return;
  }
  connectCloud(server);
});

const startupServer = new URLSearchParams(window.location.search).get("server");
if (startupServer) {
  cloudInput.value = startupServer;
  connectCloud(startupServer);
}
