import assert from "node:assert/strict";
import test from "node:test";

import { renderLobby } from "../agentsassemble/static/lobby.js";
import { state } from "../agentsassemble/static/shared.js";

class FakeElement {
  constructor(tagName, attributes = {}, ownerDocument = null) {
    this.tagName = tagName.toUpperCase();
    this.attributes = { ...attributes };
    this.ownerDocument = ownerDocument;
    this.listeners = new Map();
    this.value = attributes.value || "";
    this.checked = Boolean(attributes.checked);
    this.disabled = Boolean(attributes.disabled);
    this.dataset = {};
    this.textContent = "";
    this.scrollTop = 0;
    this.scrollHeight = 100;
    this.clientHeight = 100;
  }

  set innerHTML(html) {
    this._innerHTML = html;
    this.ownerDocument?.loadInnerHtml(html);
  }

  get innerHTML() {
    return this._innerHTML || "";
  }

  querySelector(selector) {
    return this.ownerDocument?.querySelector(selector) || null;
  }

  querySelectorAll(selector) {
    return this.ownerDocument?.querySelectorAll(selector) || [];
  }

  addEventListener(type, listener) {
    const listeners = this.listeners.get(type) || [];
    listeners.push(listener);
    this.listeners.set(type, listeners);
  }

  focus() {
    if (this.ownerDocument) this.ownerDocument.activeElement = this;
  }

  async click() {
    for (const listener of this.listeners.get("click") || []) {
      await listener({ currentTarget: this, preventDefault() {} });
    }
  }

  async submit() {
    for (const listener of this.listeners.get("submit") || []) {
      await listener({ currentTarget: this, preventDefault() {} });
    }
  }
}

class FakeDocument {
  constructor() {
    this.activeElement = null;
    this.byId = new Map();
    this.byClass = new Map();
    this.lobby = new FakeElement("div", { id: "lobby" }, this);
    this.byId.set("lobby", this.lobby);
  }

  querySelector(selector) {
    if (selector.startsWith("#")) return this.byId.get(selector.slice(1)) || null;
    if (selector.startsWith(".")) return this.byClass.get(selector.slice(1))?.[0] || null;
    return null;
  }

  querySelectorAll(selector) {
    if (selector.startsWith(".")) return this.byClass.get(selector.slice(1)) || [];
    if (selector.startsWith("[data-")) return [];
    return [];
  }

  loadInnerHtml(html) {
    this.byId = new Map([["lobby", this.lobby]]);
    this.byClass = new Map();
    for (const match of html.matchAll(/<([a-zA-Z][\w-]*)([^>]*)>/g)) {
      const [, tagName, rawAttributes] = match;
      const attributes = parseAttributes(rawAttributes);
      if (!attributes.id && !attributes.class) continue;
      const element = new FakeElement(tagName, attributes, this);
      element.textContent = elementTextContent(html, match.index + match[0].length, tagName);
      if (attributes.id) this.byId.set(attributes.id, element);
      if (attributes.class) {
        for (const className of attributes.class.split(/\s+/).filter(Boolean)) {
          const elements = this.byClass.get(className) || [];
          elements.push(element);
          this.byClass.set(className, elements);
        }
      }
    }
  }
}

function elementTextContent(html, contentStart, tagName) {
  const closeIndex = html.indexOf(`</${tagName}>`, contentStart);
  if (closeIndex < 0) return "";
  return unescapeHtml(html.slice(contentStart, closeIndex).replaceAll(/<[^>]*>/g, "").trim());
}

function parseAttributes(rawAttributes) {
  const attributes = {};
  for (const [, name, quotedValue, bareValue] of rawAttributes.matchAll(/([\w-]+)(?:="([^"]*)"|=(\S+))?/g)) {
    attributes[name] = quotedValue ?? bareValue ?? true;
  }
  return attributes;
}

function unescapeHtml(value) {
  return value
    .replaceAll("&amp;", "&")
    .replaceAll("&lt;", "<")
    .replaceAll("&gt;", ">")
    .replaceAll("&quot;", '"')
    .replaceAll("&#039;", "'");
}

function resetState() {
  Object.assign(state, {
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
    liveAgentsLoaded: true,
    liveAgentsLoading: false,
    liveAgentStatus: null,
    liveAgentProbeRunning: "",
    liveAgentProcesses: [],
    liveAgentProcessesLoaded: true,
    liveAgentProcessesLoading: false,
    liveAgentOperations: [],
    liveAgentOperationsLoaded: true,
    liveAgentOperationsLoading: false,
    liveAgentProcessStartRunning: false,
    liveAgentSessionStartRunning: false,
    liveAgentPreflightRunning: false,
    liveAgentSmokeRunning: false,
    liveAgentOfficialRoundSmokeRunning: false,
    liveAgentReadinessRunning: false,
    liveAgentProcessStatus: null,
    codexSessions: [],
    codexSessionsLoaded: true,
    codexSessionsLoading: false,
    codexInviteStatus: null,
  });
}

function installHarness({
  readinessPayload,
  processStartPayload = null,
  sessionStartPayload = null,
  sessionStartResponse = null,
} = {}) {
  const requests = [];
  const events = [];
  const document = new FakeDocument();
  const storage = new Map();
  globalThis.document = document;
  globalThis.localStorage = {
    getItem(key) {
      return storage.get(key) || "";
    },
    setItem(key, value) {
      storage.set(key, String(value));
    },
  };
  globalThis.requestAnimationFrame = (callback) => callback();
  globalThis.CustomEvent = class {
    constructor(type, init = {}) {
      this.type = type;
      this.detail = init.detail || {};
    }
  };
  globalThis.dispatchEvent = (event) => {
    events.push(event);
    return true;
  };
  globalThis.fetch = async (url, options = {}) => {
    requests.push({
      url: String(url),
      options,
      jsonBody: options.body ? JSON.parse(options.body) : null,
    });
    if (url === "/api/live-agent-readiness") return jsonResponse(readinessPayload);
    if (url === "/api/live-agent-processes/start") {
      return jsonResponse(processStartPayload || { group: { group_id: "crew", status: "running" }, groups: [] });
    }
    if (url === "/api/live-agent-sessions/start") {
      if (sessionStartResponse) {
        return jsonResponse(sessionStartResponse.payload, {
          ok: sessionStartResponse.ok,
          status: sessionStartResponse.status,
        });
      }
      return jsonResponse(
        sessionStartPayload || {
          status: "ready",
          meeting_id: "resident-gui",
          group_id: "resident-main",
          connection: { expected: 3, connected: 3, attention: [] },
          process: { status: "running", attention: [] },
        }
      );
    }
    if (url === "/api/lobby") return jsonResponse({ events: [] });
    if (url === "/api/live-agents") return jsonResponse({ agents: [] });
    if (url === "/api/live-agent-processes") return jsonResponse({ groups: [] });
    if (url === "/api/live-agent-operations?limit=20") return jsonResponse({ operations: [] });
    return jsonResponse({});
  };
  return { document, requests, events };
}

function jsonResponse(payload, { ok = true, status = 200 } = {}) {
  return {
    ok,
    status,
    json: async () => payload,
    headers: { get: () => "application/json" },
  };
}

function readinessRequest(requests) {
  return requests.find((request) => request.url === "/api/live-agent-readiness");
}

function processStartRequest(requests) {
  return requests.find((request) => request.url === "/api/live-agent-processes/start");
}

function sessionStartRequest(requests) {
  return requests.find((request) => request.url === "/api/live-agent-sessions/start");
}

async function clickReadiness({ officialRoundSmoke, readinessPayload }) {
  resetState();
  const { document, requests } = installHarness({ readinessPayload });
  renderLobby({ followLatest: false });
  const lobby = document.querySelector("#lobby");
  lobby.querySelector("#live-agent-process-group").value = "doctor-smoke";
  lobby.querySelector("#live-agent-readiness-official-round").checked = officialRoundSmoke;
  await lobby.querySelector("#live-agent-readiness-check").click();
  return { document, requests };
}

test("readiness button omits official round smoke when the checkbox is unchecked", async () => {
  const { requests } = await clickReadiness({
    officialRoundSmoke: false,
    readinessPayload: { status: "ready" },
  });

  const request = readinessRequest(requests);
  assert.deepEqual(request.jsonBody, { group_id: "doctor-smoke", timeout: 12 });
  assert.equal(Object.hasOwn(request.jsonBody, "official_round_smoke"), false);
});

test("readiness button sends official round smoke and reports the official counts when checked", async () => {
  const { document, requests } = await clickReadiness({
    officialRoundSmoke: true,
    readinessPayload: {
      status: "ready",
      official_round_smoke: {
        status: "ok",
        answered_count: 2,
        timeout_count: 1,
        skipped_count: 0,
      },
    },
  });

  const request = readinessRequest(requests);
  assert.deepEqual(request.jsonBody, {
    group_id: "doctor-smoke",
    timeout: 12,
    official_round_smoke: true,
  });
  assert.equal(
    state.liveAgentProcessStatus.message,
    "readiness ready · official 2 answered, 1 timed out, 0 skipped"
  );
  assert.equal(
    document.querySelector(".live-agent-status").textContent,
    "readiness ready · official 2 answered, 1 timed out, 0 skipped"
  );
});

test("process start form preserves and posts stale watchdog seconds", async () => {
  resetState();
  const { document, requests } = installHarness();
  renderLobby({ followLatest: false });
  let lobby = document.querySelector("#lobby");
  lobby.querySelector("#live-agent-process-config").value = "configs/live-agents.example.json";
  lobby.querySelector("#live-agent-process-group").value = "crew";
  lobby.querySelector("#live-agent-process-auto-restart").checked = true;
  lobby.querySelector("#live-agent-process-max-restarts").value = "2";
  lobby.querySelector("#live-agent-process-restart-backoff").value = "1.5";
  lobby.querySelector("#live-agent-process-stale-restart-after").value = "240";

  renderLobby({ followLatest: false });
  lobby = document.querySelector("#lobby");
  assert.equal(lobby.querySelector("#live-agent-process-stale-restart-after").value, "240");

  await lobby.querySelector("#live-agent-process-form").submit();

  assert.deepEqual(processStartRequest(requests).jsonBody, {
    config_path: "configs/live-agents.example.json",
    group_id: "crew",
    auto_restart: true,
    max_restarts: 2,
    restart_backoff_seconds: 1.5,
    stale_restart_after_seconds: 240,
  });
});

test("session start button posts matching meeting and resident config payload", async () => {
  resetState();
  const { document, requests, events } = installHarness({
    sessionStartPayload: {
      status: "ready",
      meeting_id: "resident-gui",
      group_id: "resident-main",
      connection: { expected: 3, connected: 3, attention: [] },
      process: { status: "running", attention: [] },
    },
  });
  renderLobby({ followLatest: false });
  const lobby = document.querySelector("#lobby");
  lobby.querySelector("#live-agent-session-meeting-id").value = "resident-gui";
  lobby.querySelector("#live-agent-session-council-config").value = "configs/demo-council.json";
  lobby.querySelector("#live-agent-session-agent-config").value = "configs/agents.start-session.example.json";
  lobby.querySelector("#live-agent-process-config").value = "configs/live-agents.start-session.example.json";
  lobby.querySelector("#live-agent-process-group").value = "resident-main";
  lobby.querySelector("#live-agent-session-connect-timeout").value = "7";
  lobby.querySelector("#live-agent-process-auto-restart").checked = true;
  lobby.querySelector("#live-agent-process-max-restarts").value = "4";
  lobby.querySelector("#live-agent-process-restart-backoff").value = "2";
  lobby.querySelector("#live-agent-process-stale-restart-after").value = "300";

  await lobby.querySelector("#live-agent-session-start").click();

  assert.deepEqual(sessionStartRequest(requests).jsonBody, {
    meeting_id: "resident-gui",
    group_id: "resident-main",
    council_config_path: "configs/demo-council.json",
    agent_config_path: "configs/agents.start-session.example.json",
    live_agent_config_path: "configs/live-agents.start-session.example.json",
    connect_timeout_seconds: 7,
    auto_restart: true,
    max_restarts: 4,
    restart_backoff_seconds: 2,
    stale_restart_after_seconds: 300,
  });
  assert.equal(state.liveAgentProcessStatus.message, "세션 ready: resident-gui · 3/3 connected");
  assert.equal(events.at(-1)?.type, "agentsassemble:meeting-started");
  assert.equal(events.at(-1)?.detail.meetingId, "resident-gui");
});

test("session start failure with created meeting still announces the recoverable meeting", async () => {
  resetState();
  const { document, requests, events } = installHarness({
    sessionStartResponse: {
      ok: false,
      status: 400,
      payload: {
        error: "resident process failed after meeting creation",
        meeting_id: "recoverable-meeting",
        recoverable_meeting_id: "recoverable-meeting",
        details: { meeting_id: "recoverable-meeting", recoverable_meeting_id: "recoverable-meeting" },
      },
    },
  });
  renderLobby({ followLatest: false });
  const lobby = document.querySelector("#lobby");
  lobby.querySelector("#live-agent-session-meeting-id").value = "recoverable-meeting";
  lobby.querySelector("#live-agent-session-connect-timeout").value = "999";

  await lobby.querySelector("#live-agent-session-start").click();

  assert.equal(sessionStartRequest(requests).jsonBody.connect_timeout_seconds, 120);
  assert.equal(state.liveAgentProcessStatus.message, "상주 세션 시작 실패: resident process failed after meeting creation");
  assert.equal(events.at(-1)?.type, "agentsassemble:meeting-started");
  assert.equal(events.at(-1)?.detail.meetingId, "recoverable-meeting");
});

test("session start refusal before meeting creation does not announce requested meeting", async () => {
  resetState();
  const { document, events } = installHarness({
    sessionStartResponse: {
      ok: false,
      status: 400,
      payload: {
        error: "Live agent preflight failed",
        details: { requested_meeting_id: "not-created" },
      },
    },
  });
  renderLobby({ followLatest: false });
  const lobby = document.querySelector("#lobby");
  lobby.querySelector("#live-agent-session-meeting-id").value = "not-created";

  await lobby.querySelector("#live-agent-session-start").click();

  assert.equal(state.liveAgentProcessStatus.message, "상주 세션 시작 실패: Live agent preflight failed");
  assert.equal(events.length, 0);
});

test("process row renders recovery watchdog and next restart evidence", () => {
  resetState();
  const { document } = installHarness();
  state.liveAgentProcesses = [
    {
      group_id: "crew",
      status: "restarting",
      pid: "",
      config_path: "configs/live-agents.example.json",
      server: "http://127.0.0.1:8765",
      auto_restart: true,
      restart_count: 1,
      max_restarts: 3,
      restart_backoff_seconds: 5,
      stale_restart_after_seconds: 240,
      next_restart_at: "2026-05-17T12:01:00+00:00",
    },
  ];

  renderLobby({ followLatest: false });

  const rowText = document.querySelector(".live-agent-process-row").textContent;
  assert.match(rowText, /auto restart 1\/3/);
  assert.match(rowText, /stale watchdog 240s/);
  assert.match(rowText, /next restart 2026-05-17T12:01:00\+00:00/);
});

test("process row omits disabled recovery fields", () => {
  resetState();
  const { document } = installHarness();
  state.liveAgentProcesses = [
    {
      group_id: "crew",
      status: "running",
      pid: 1234,
      config_path: "configs/live-agents.example.json",
      server: "http://127.0.0.1:8765",
      auto_restart: true,
      restart_count: 0,
      max_restarts: 3,
      restart_backoff_seconds: 5,
      stale_restart_after_seconds: 0,
      next_restart_at: "",
    },
  ];

  renderLobby({ followLatest: false });

  const rowText = document.querySelector(".live-agent-process-row").textContent;
  assert.doesNotMatch(rowText, /stale watchdog/);
  assert.doesNotMatch(rowText, /next restart/);
});

test("operation row renders safe details when summary is empty", () => {
  resetState();
  const { document } = installHarness();
  state.liveAgentOperations = [
    {
      timestamp: "2026-05-18T01:02:03+00:00",
      operation: "readiness.check",
      status: "degraded",
      target_id: "doctor-smoke",
      summary: "",
      details: {
        result_status: "degraded",
        smoke_reply_count: 3,
        probe_agent_ids: ["agent-a", "agent-b"],
      },
    },
  ];

  renderLobby({ followLatest: false });

  const rowText = document.querySelector(".live-agent-operation-row").textContent;
  assert.match(rowText, /readiness\.check/);
  assert.match(rowText, /result_status=degraded/);
  assert.match(rowText, /smoke_reply_count=3/);
  assert.match(rowText, /probe_agent_ids=agent-a,agent-b/);
});
