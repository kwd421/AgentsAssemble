import assert from "node:assert/strict";
import test from "node:test";

import { refreshLiveAgentRuntimeSurfaces, renderLobby } from "../agentsassemble/static/lobby.js";
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
    this.dataset = datasetFromAttributes(attributes);
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
    this.byData = new Map();
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
    if (selector.startsWith("[data-")) {
      const attributeName = selector.slice(1, -1);
      return this.byData.get(attributeName) || [];
    }
    return [];
  }

  loadInnerHtml(html) {
    this.byId = new Map([["lobby", this.lobby]]);
    this.byClass = new Map();
    this.byData = new Map();
    for (const match of html.matchAll(/<([a-zA-Z][\w-]*)([^>]*)>/g)) {
      const [, tagName, rawAttributes] = match;
      const attributes = parseAttributes(rawAttributes);
      const dataAttributeNames = Object.keys(attributes).filter((name) => name.startsWith("data-"));
      if (!attributes.id && !attributes.class && dataAttributeNames.length === 0) continue;
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
      for (const dataAttributeName of dataAttributeNames) {
        const elements = this.byData.get(dataAttributeName) || [];
        elements.push(element);
        this.byData.set(dataAttributeName, elements);
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

function datasetFromAttributes(attributes) {
  const dataset = {};
  for (const [name, value] of Object.entries(attributes)) {
    if (!name.startsWith("data-")) continue;
    const key = name
      .slice(5)
      .replaceAll(/-([a-z])/g, (_, letter) => letter.toUpperCase());
    dataset[key] = String(value);
  }
  return dataset;
}

function unescapeHtml(value) {
  return value
    .replaceAll("&amp;", "&")
    .replaceAll("&lt;", "<")
    .replaceAll("&gt;", ">")
    .replaceAll("&quot;", '"')
    .replaceAll("&#039;", "'");
}

function deferred() {
  let resolve;
  const promise = new Promise((done) => {
    resolve = done;
  });
  return { promise, resolve };
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
    liveAgentHealth: null,
    liveAgentHealthLoaded: true,
    liveAgentHealthLoading: false,
    liveAgentProcesses: [],
    liveAgentProcessesLoaded: true,
    liveAgentProcessesLoading: false,
    liveAgentOperations: [],
    liveAgentOperationsLoaded: true,
    liveAgentOperationsLoading: false,
    liveAgentProcessStartRunning: false,
    liveAgentSessionStartRunning: false,
    liveAgentSessionRestartRunning: false,
    liveAgentSessionRecoverRunning: false,
    liveAgentSessionCheckRunning: false,
    liveAgentSessionStopRunning: false,
    liveAgentPreflightRunning: false,
    liveAgentSmokeRunning: false,
    liveAgentOfficialRoundSmokeRunning: false,
    liveAgentSessionSmokeRunning: false,
    liveAgentReadinessRunning: false,
    liveAgentProcessRowActionRunning: "",
    liveAgentProcessBulkStopRunning: false,
    liveAgentRoundCallRunning: false,
    liveAgentProcessStatus: null,
    codexSessions: [],
    codexSessionsLoaded: true,
    codexSessionsLoading: false,
    codexInviteStatus: null,
  });
}

function installHarness({
  readinessPayload,
  healthPayload = null,
  healthResponse = null,
  processStartPayload = null,
  processStopRunningPayload = null,
  sessionStartPayload = null,
  sessionResumePayload = null,
  sessionRestartPayload = null,
  sessionRecoverPayload = null,
  sessionStopPayload = null,
  sessionCheckPayload = null,
  sessionSmokePayload = null,
  sessionSmokeResponse = null,
  sessionStartResponse = null,
  processActionGate = null,
  roundPayload = null,
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
    if (url === "/api/live-agent-health") {
      return jsonResponse(
        healthResponse?.payload ||
          healthPayload || {
          status: "ok",
          agents: { total: 0, live: 0, counts: { online: 0, working: 0, error: 0, stale: 0, offline: 0 }, attention: [] },
          processes: { total: 0, counts: { running: 0, restarting: 0, error: 0, unknown: 0, stopped: 0 }, attention: [] },
          connections: { expected: 0, connected: 0, attention: [] },
        },
        {
          ok: healthResponse?.ok ?? true,
          status: healthResponse?.status ?? 200,
        }
      );
    }
    if (url === "/api/live-agent-processes/start") {
      return jsonResponse(processStartPayload || { group: { group_id: "crew", status: "running" }, groups: [] });
    }
    if (url === "/api/live-agent-processes/stop-running") {
      return jsonResponse(
        processStopRunningPayload || {
          result: { stopped_count: 2, failed_count: 0, skipped_count: 0, stopped: [], failed: [], skipped: [] },
          groups: [],
        }
      );
    }
    const processActionMatch = String(url).match(/^\/api\/live-agent-processes\/([^/]+)\/(stop|restart|recover)$/);
    if (processActionMatch) {
      if (processActionGate) await processActionGate.promise;
      const [, groupId, action] = processActionMatch;
      if (action === "recover") {
        return jsonResponse({
          group: { group_id: groupId, status: "running", pid: 6789, recovered_from_status: "unknown" },
          groups: [{ group_id: groupId, status: "running", pid: 6789, recovered_from_status: "unknown" }],
        });
      }
      return jsonResponse({
        group: { group_id: groupId, status: action === "stop" ? "stopped" : "running" },
        groups: [{ group_id: groupId, status: action === "stop" ? "stopped" : "running" }],
      });
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
    if (url === "/api/live-agent-sessions/resume") {
      return jsonResponse(
        sessionResumePayload || {
          status: "ready",
          meeting_id: "resident-gui",
          group_id: "resident-main",
          connection: { expected: 3, connected: 3, attention: [] },
          process: { status: "running", attention: [] },
        }
      );
    }
    if (url === "/api/live-agent-sessions/restart") {
      return jsonResponse(
        sessionRestartPayload || {
          status: "ready",
          meeting_id: "resident-gui",
          group_id: "resident-main",
          connection: { expected: 3, connected: 3, attention: [] },
          process: { status: "running", attention: [] },
        }
      );
    }
    if (url === "/api/live-agent-sessions/recover") {
      return jsonResponse(
        sessionRecoverPayload || {
          status: "ready",
          meeting_id: "resident-gui",
          group_id: "resident-main",
          connection: { expected: 3, connected: 3, attention: [] },
          offline: { expected: 3, offline: 3, attention: [] },
          process: { status: "running", attention: [] },
        }
      );
    }
    if (url === "/api/live-agent-sessions/stop") {
      return jsonResponse(
        sessionStopPayload || {
          status: "stopped",
          meeting_id: "resident-gui",
          group_id: "resident-main",
          offline: { expected: 3, offline: 3, attention: [] },
          process: { status: "stopped", attention: [] },
        }
      );
    }
    if (url === "/api/live-agent-sessions/check") {
      return jsonResponse(
        sessionCheckPayload || {
          status: "ready",
          meeting_id: "resident-gui",
          group_id: "resident-main",
          connection: { expected: 3, connected: 3, attention: [] },
          process: { status: "running", attention: [] },
        }
      );
    }
    if (url === "/api/live-agent-session-smoke") {
      const payload = sessionSmokeResponse?.payload ||
        sessionSmokePayload || {
          status: "ok",
          meeting_id: "session-smoke-123",
          group_id: "session-smoke-123",
          rounds_status: "answered",
          round_count: 1,
          answered_round_count: 1,
          expected_reply_count: 3,
          reply_count: 3,
          post_restart_reply_count: 3,
          post_recover_reply_count: 3,
          start_status: "ready",
          check_status: "ready",
          resume_status: "ready",
          restart_status: "ready",
          recover_status: "ready",
          stop_status: "stopped",
        };
      return jsonResponse(payload, {
        ok: sessionSmokeResponse?.ok ?? true,
        status: sessionSmokeResponse?.status ?? 200,
      });
    }
    if (url === "/api/meetings/resident-gui/live-agent-turns/round") {
      return jsonResponse(
        roundPayload || {
          status: "answered",
          meeting_id: "resident-gui",
          round_id: "round_1",
          turn_count: 3,
          answered_count: 3,
          timeout_count: 0,
          skipped_count: 0,
        }
      );
    }
    if (url === "/api/meetings/resident-gui/live-agent-turns/rounds") {
      return jsonResponse({
        status: "answered",
        meeting_id: "resident-gui",
        round_count: 1,
        answered_round_count: 1,
        timeout_round_count: 0,
        skipped_round_count: 0,
        results: [{ round_id: "round_2", status: "answered" }],
      });
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

function sessionResumeRequest(requests) {
  return requests.find((request) => request.url === "/api/live-agent-sessions/resume");
}

function sessionRestartRequest(requests) {
  return requests.find((request) => request.url === "/api/live-agent-sessions/restart");
}

function sessionRecoverRequest(requests) {
  return requests.find((request) => request.url === "/api/live-agent-sessions/recover");
}

function sessionStopRequest(requests) {
  return requests.find((request) => request.url === "/api/live-agent-sessions/stop");
}

function sessionCheckRequest(requests) {
  return requests.find((request) => request.url === "/api/live-agent-sessions/check");
}

function sessionSmokeRequest(requests) {
  return requests.find((request) => request.url === "/api/live-agent-session-smoke");
}

function roundRequest(requests) {
  return requests.find((request) => request.url === "/api/meetings/resident-gui/live-agent-turns/round");
}

function remainingRoundsRequest(requests) {
  return requests.find((request) => request.url === "/api/meetings/resident-gui/live-agent-turns/rounds");
}

async function clickReadiness({ officialRoundSmoke, sessionSmoke = false, readinessPayload }) {
  resetState();
  const { document, requests } = installHarness({ readinessPayload });
  renderLobby({ followLatest: false });
  const lobby = document.querySelector("#lobby");
  lobby.querySelector("#live-agent-process-group").value = "doctor-smoke";
  lobby.querySelector("#live-agent-readiness-official-round").checked = officialRoundSmoke;
  lobby.querySelector("#live-agent-readiness-session-smoke").checked = sessionSmoke;
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
  assert.equal(Object.hasOwn(request.jsonBody, "session_smoke"), false);
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

test("readiness button sends session smoke and reports the session counts when checked", async () => {
  const { requests } = await clickReadiness({
    officialRoundSmoke: false,
    sessionSmoke: true,
    readinessPayload: {
      status: "ready",
      session_smoke: {
        status: "ok",
        group_id: "session-smoke",
        expected_reply_count: 3,
        lobby_probe_count: 1,
        reply_count: 3,
        post_restart_reply_count: 3,
        post_recover_reply_count: 3,
      },
    },
  });

  const request = readinessRequest(requests);
  assert.deepEqual(request.jsonBody, {
    group_id: "doctor-smoke",
    timeout: 12,
    session_smoke: true,
  });
  assert.equal(
    state.liveAgentProcessStatus.message,
    "readiness ready · session 3/3 replies, post-restart 3/3, post-recover 3/3"
  );
});

test("readiness button sends bounded session smoke soak controls when configured", async () => {
  resetState();
  const { document, requests } = installHarness({
    readinessPayload: {
      status: "ready",
      session_smoke: {
        status: "ok",
        group_id: "session-smoke",
        expected_reply_count: 3,
        lobby_probe_count: 1,
        reply_count: 3,
        post_restart_reply_count: 3,
        post_recover_reply_count: 3,
        soak_cycle_count: 2,
        soak_reply_count: 6,
      },
    },
  });
  renderLobby({ followLatest: false });
  let lobby = document.querySelector("#lobby");
  lobby.querySelector("#live-agent-process-group").value = "doctor-smoke";
  lobby.querySelector("#live-agent-readiness-session-smoke").checked = true;
  lobby.querySelector("#live-agent-session-smoke-soak-cycles").value = "2";
  lobby.querySelector("#live-agent-session-smoke-soak-interval").value = "0.5";

  renderLobby({ followLatest: false });
  lobby = document.querySelector("#lobby");
  assert.equal(lobby.querySelector("#live-agent-session-smoke-soak-cycles").value, "2");
  assert.equal(lobby.querySelector("#live-agent-session-smoke-soak-interval").value, "0.5");

  await lobby.querySelector("#live-agent-readiness-check").click();

  const request = readinessRequest(requests);
  assert.deepEqual(request.jsonBody, {
    group_id: "doctor-smoke",
    timeout: 12,
    session_smoke: true,
    session_smoke_soak_cycle_count: 2,
    session_smoke_soak_interval_seconds: 0.5,
  });
  assert.equal(
    state.liveAgentProcessStatus.message,
    "readiness ready · session 3/3 replies, post-restart 3/3, post-recover 3/3, soak 6/6 over 2 cycles"
  );
});

test("readiness status shows skipped session smoke reason", async () => {
  await clickReadiness({
    officialRoundSmoke: false,
    sessionSmoke: true,
    readinessPayload: {
      status: "failed",
      session_smoke: {
        status: "skipped",
        reason: "smoke did not pass",
      },
    },
  });

  assert.equal(
    state.liveAgentProcessStatus.message,
    "readiness failed · session skipped: smoke did not pass"
  );
});

test("readiness status shows failed session smoke error", async () => {
  await clickReadiness({
    officialRoundSmoke: false,
    sessionSmoke: true,
    readinessPayload: {
      status: "failed",
      session_smoke: {
        status: "failed",
        error: "session smoke could not be run",
      },
    },
  });

  assert.equal(
    state.liveAgentProcessStatus.message,
    "readiness failed · session failed: session smoke could not be run"
  );
});

test("readiness status can show official and session smoke evidence together", async () => {
  const { requests } = await clickReadiness({
    officialRoundSmoke: true,
    sessionSmoke: true,
    readinessPayload: {
      status: "ready",
      official_round_smoke: {
        status: "ok",
        answered_count: 2,
        timeout_count: 0,
        skipped_count: 0,
      },
      session_smoke: {
        status: "ok",
        group_id: "session-smoke",
        expected_reply_count: 3,
        lobby_probe_count: 1,
        reply_count: 3,
        post_restart_reply_count: 3,
        post_recover_reply_count: 3,
      },
    },
  });

  const request = readinessRequest(requests);
  assert.deepEqual(request.jsonBody, {
    group_id: "doctor-smoke",
    timeout: 12,
    official_round_smoke: true,
    session_smoke: true,
  });
  assert.equal(
    state.liveAgentProcessStatus.message,
    "readiness ready · official 2 answered, 0 timed out, 0 skipped · session 3/3 replies, post-restart 3/3, post-recover 3/3"
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
      reply_probe: {
        status: "ok",
        probe_count: 3,
        ok_count: 3,
        timeout_count: 0,
        failed_count: 0,
        skipped_count: 0,
      },
      auto_rounds: {
        status: "answered",
        round_count: 2,
        answered_round_count: 1,
        completed_round_count: 1,
        timeout_round_count: 0,
        skipped_round_count: 0,
      },
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
  lobby.querySelector("#live-agent-session-run-remaining-rounds").checked = true;
  lobby.querySelector("#live-agent-session-probe-bound-agents").checked = true;
  lobby.querySelector("#live-agent-session-probe-timeout").value = "4";
  lobby.querySelector("#live-agent-round-timeout").value = "12";
  lobby.querySelector("#live-agent-round-max-rounds").value = "2";
  lobby.querySelector("#live-agent-round-stop-on-timeout").checked = true;

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
    probe_bound_agents: true,
    probe_timeout_seconds: 4,
    run_remaining_rounds: true,
    round_timeout_seconds: 12,
    round_max_rounds: 2,
    round_stop_on_timeout: true,
  });
  assert.equal(
    state.liveAgentProcessStatus.message,
    "세션 ready: resident-gui · 3/3 connected · probes ok: 3/3 ok · rounds answered: 2 rounds, 1 answered, 1 already complete, 0 timed out, 0 skipped"
  );
  assert.equal(events.at(-1)?.type, "agentsassemble:meeting-started");
  assert.equal(events.at(-1)?.detail.meetingId, "resident-gui");
});

test("session resume button posts existing meeting and resident config payload", async () => {
  resetState();
  const { document, requests, events } = installHarness({
    sessionResumePayload: {
      status: "ready",
      meeting_id: "resident-gui",
      group_id: "resident-main",
      connection: { expected: 3, connected: 3, attention: [] },
      process: { status: "running", attention: [] },
      reply_probe: {
        status: "ok",
        probe_count: 3,
        ok_count: 3,
        timeout_count: 0,
        failed_count: 0,
        skipped_count: 0,
      },
      auto_rounds: {
        status: "answered",
        round_count: 1,
        answered_round_count: 1,
        completed_round_count: 0,
        timeout_round_count: 0,
        skipped_round_count: 0,
      },
    },
  });
  renderLobby({ followLatest: false });
  const lobby = document.querySelector("#lobby");
  lobby.querySelector("#live-agent-session-meeting-id").value = "resident-gui";
  lobby.querySelector("#live-agent-process-config").value = "configs/live-agents.start-session.example.json";
  lobby.querySelector("#live-agent-process-group").value = "resident-main";
  lobby.querySelector("#live-agent-session-connect-timeout").value = "7";
  lobby.querySelector("#live-agent-process-auto-restart").checked = true;
  lobby.querySelector("#live-agent-process-max-restarts").value = "4";
  lobby.querySelector("#live-agent-process-restart-backoff").value = "2";
  lobby.querySelector("#live-agent-process-stale-restart-after").value = "300";
  lobby.querySelector("#live-agent-session-run-remaining-rounds").checked = true;
  lobby.querySelector("#live-agent-session-probe-bound-agents").checked = true;
  lobby.querySelector("#live-agent-session-probe-timeout").value = "4";
  lobby.querySelector("#live-agent-round-timeout").value = "12";
  lobby.querySelector("#live-agent-round-max-rounds").value = "2";
  lobby.querySelector("#live-agent-round-stop-on-timeout").checked = true;

  await lobby.querySelector("#live-agent-session-resume").click();

  assert.deepEqual(sessionResumeRequest(requests).jsonBody, {
    meeting_id: "resident-gui",
    group_id: "resident-main",
    live_agent_config_path: "configs/live-agents.start-session.example.json",
    connect_timeout_seconds: 7,
    auto_restart: true,
    max_restarts: 4,
    restart_backoff_seconds: 2,
    stale_restart_after_seconds: 300,
    probe_bound_agents: true,
    probe_timeout_seconds: 4,
    run_remaining_rounds: true,
    round_timeout_seconds: 12,
    round_max_rounds: 2,
    round_stop_on_timeout: true,
  });
  assert.equal(
    state.liveAgentProcessStatus.message,
    "세션 ready: resident-gui · 3/3 connected · probes ok: 3/3 ok · rounds answered: 1 rounds, 1 answered, 0 timed out, 0 skipped"
  );
  assert.equal(events.at(-1)?.type, "agentsassemble:meeting-started");
  assert.equal(events.at(-1)?.detail.meetingId, "resident-gui");
});

test("session restart button posts existing meeting group and timeout payload", async () => {
  resetState();
  const { document, requests, events } = installHarness({
    sessionRestartPayload: {
      status: "ready",
      meeting_id: "resident-gui",
      group_id: "resident-main",
      connection: { expected: 3, connected: 3, attention: [] },
      process: { status: "running", attention: [] },
      reply_probe: {
        status: "failed",
        probe_count: 3,
        ok_count: 2,
        timeout_count: 1,
        failed_count: 0,
        skipped_count: 0,
      },
      auto_rounds: {
        status: "skipped",
        reason: "probe_not_ready",
        round_count: 0,
        answered_round_count: 0,
        completed_round_count: 0,
        timeout_round_count: 0,
        skipped_round_count: 0,
      },
    },
  });
  renderLobby({ followLatest: false });
  const lobby = document.querySelector("#lobby");
  lobby.querySelector("#live-agent-session-meeting-id").value = "resident-gui";
  lobby.querySelector("#live-agent-process-group").value = "resident-main";
  lobby.querySelector("#live-agent-session-connect-timeout").value = "7";
  lobby.querySelector("#live-agent-session-run-remaining-rounds").checked = true;
  lobby.querySelector("#live-agent-session-probe-bound-agents").checked = true;
  lobby.querySelector("#live-agent-session-probe-timeout").value = "4";
  lobby.querySelector("#live-agent-round-timeout").value = "12";
  lobby.querySelector("#live-agent-round-max-rounds").value = "2";
  lobby.querySelector("#live-agent-round-stop-on-timeout").checked = true;

  await lobby.querySelector("#live-agent-session-restart").click();

  assert.deepEqual(sessionRestartRequest(requests).jsonBody, {
    meeting_id: "resident-gui",
    group_id: "resident-main",
    connect_timeout_seconds: 7,
    probe_bound_agents: true,
    probe_timeout_seconds: 4,
    run_remaining_rounds: true,
    round_timeout_seconds: 12,
    round_max_rounds: 2,
    round_stop_on_timeout: true,
  });
  assert.equal(
    state.liveAgentProcessStatus.message,
    "세션 ready: resident-gui · 3/3 connected · probes failed: 2/3 ok · rounds skipped: 0 rounds, 0 answered, 0 timed out, 0 skipped"
  );
  assert.equal(
    requests.some((request) => request.url === "/api/live-agent-processes"),
    true
  );
  assert.equal(
    requests.some((request) => request.url === "/api/live-agents"),
    true
  );
  assert.equal(events.at(-1)?.type, "agentsassemble:meeting-started");
  assert.equal(events.at(-1)?.detail.meetingId, "resident-gui");
});

test("session recover button posts existing meeting group and timeout payload", async () => {
  resetState();
  const { document, requests, events } = installHarness({
    sessionRecoverPayload: {
      status: "ready",
      meeting_id: "resident-gui",
      group_id: "resident-main",
      connection: { expected: 3, connected: 3, attention: [] },
      offline: { expected: 3, offline: 3, attention: [] },
      process: { status: "running", attention: [] },
      reply_probe: {
        status: "ok",
        probe_count: 3,
        ok_count: 3,
        timeout_count: 0,
        failed_count: 0,
        skipped_count: 0,
      },
      auto_rounds: {
        status: "answered",
        round_count: 1,
        answered_round_count: 1,
        completed_round_count: 0,
        timeout_round_count: 0,
        skipped_round_count: 0,
      },
    },
  });
  renderLobby({ followLatest: false });
  const lobby = document.querySelector("#lobby");
  lobby.querySelector("#live-agent-session-meeting-id").value = "resident-gui";
  lobby.querySelector("#live-agent-process-group").value = "resident-main";
  lobby.querySelector("#live-agent-session-connect-timeout").value = "7";
  lobby.querySelector("#live-agent-session-run-remaining-rounds").checked = true;
  lobby.querySelector("#live-agent-session-probe-bound-agents").checked = true;
  lobby.querySelector("#live-agent-session-probe-timeout").value = "4";
  lobby.querySelector("#live-agent-round-timeout").value = "12";
  lobby.querySelector("#live-agent-round-max-rounds").value = "2";
  lobby.querySelector("#live-agent-round-stop-on-timeout").checked = true;

  await lobby.querySelector("#live-agent-session-recover").click();

  assert.deepEqual(sessionRecoverRequest(requests).jsonBody, {
    meeting_id: "resident-gui",
    group_id: "resident-main",
    connect_timeout_seconds: 7,
    probe_bound_agents: true,
    probe_timeout_seconds: 4,
    run_remaining_rounds: true,
    round_timeout_seconds: 12,
    round_max_rounds: 2,
    round_stop_on_timeout: true,
  });
  assert.equal(
    state.liveAgentProcessStatus.message,
    "세션 ready: resident-gui · 3/3 connected · probes ok: 3/3 ok · rounds answered: 1 rounds, 1 answered, 0 timed out, 0 skipped"
  );
  assert.equal(
    requests.some((request) => request.url === "/api/live-agent-processes"),
    true
  );
  assert.equal(
    requests.some((request) => request.url === "/api/live-agents"),
    true
  );
  assert.equal(events.at(-1)?.type, "agentsassemble:meeting-started");
  assert.equal(events.at(-1)?.detail.meetingId, "resident-gui");
});

test("session stop button posts existing meeting and group payload", async () => {
  resetState();
  const { document, requests } = installHarness({
    sessionStopPayload: {
      status: "stopped",
      meeting_id: "resident-gui",
      group_id: "resident-main",
      offline: { expected: 3, offline: 3, attention: [] },
      process: { status: "stopped", attention: [] },
    },
  });
  renderLobby({ followLatest: false });
  const lobby = document.querySelector("#lobby");
  lobby.querySelector("#live-agent-session-meeting-id").value = "resident-gui";
  lobby.querySelector("#live-agent-process-group").value = "resident-main";

  await lobby.querySelector("#live-agent-session-stop").click();

  assert.deepEqual(sessionStopRequest(requests).jsonBody, {
    meeting_id: "resident-gui",
    group_id: "resident-main",
  });
  assert.equal(
    state.liveAgentProcessStatus.message,
    "세션 stopped: resident-gui · resident-main · 3/3 offline"
  );
});

test("session check button posts existing meeting and group payload", async () => {
  resetState();
  const { document, requests } = installHarness({
    sessionCheckPayload: {
      status: "degraded",
      meeting_id: "resident-gui",
      group_id: "resident-main",
      connection: { expected: 3, connected: 2, attention: ["agent-c:offline"] },
      process: { status: "running", attention: [] },
    },
  });
  renderLobby({ followLatest: false });
  const lobby = document.querySelector("#lobby");
  lobby.querySelector("#live-agent-session-meeting-id").value = "resident-gui";
  lobby.querySelector("#live-agent-process-group").value = "resident-main";

  await lobby.querySelector("#live-agent-session-check").click();

  assert.deepEqual(sessionCheckRequest(requests).jsonBody, {
    meeting_id: "resident-gui",
    group_id: "resident-main",
  });
  assert.equal(
    state.liveAgentProcessStatus.message,
    "세션 degraded: resident-gui · resident-main · 2/3 connected · process running"
  );
  assert.equal(
    requests.some((request) => request.url === "/api/live-agent-processes"),
    false
  );
  assert.equal(
    requests.some((request) => request.url === "/api/live-agents"),
    false
  );
});

test("session smoke button runs fresh diagnostic session instead of reusing current meeting fields", async () => {
  resetState();
  const { document, requests } = installHarness({
    sessionSmokePayload: {
      status: "ok",
      meeting_id: "session-smoke-generated",
      group_id: "session-smoke-generated",
      rounds_status: "answered",
      round_count: 1,
      answered_round_count: 1,
      expected_reply_count: 3,
      lobby_probe_count: 2,
      reply_count: 6,
      post_restart_reply_count: 6,
      post_recover_reply_count: 6,
      start_status: "ready",
      check_status: "ready",
      resume_status: "ready",
      restart_status: "ready",
      recover_status: "ready",
      stop_status: "stopped",
    },
  });
  state.payload = { meeting: { meeting_id: "real-meeting", meeting_template: { rounds: [] }, debate_rounds: [] } };
  renderLobby({ followLatest: false });
  const lobby = document.querySelector("#lobby");
  lobby.querySelector("#live-agent-session-meeting-id").value = "real-meeting";
  lobby.querySelector("#live-agent-process-group").value = "resident-main";

  await lobby.querySelector("#live-agent-session-smoke").click();

  assert.deepEqual(sessionSmokeRequest(requests).jsonBody, { timeout: 12 });
  assert.equal(
    state.liveAgentProcessStatus.message,
    "세션 smoke ok: session-smoke-generated · rounds answered (1 answered) · 2 probes · 6/6 replies · post-restart 6/6 replies · post-recover 6/6 replies · start ready, check ready, resume ready, restart ready, recover ready, stop stopped"
  );
  assert.equal(
    requests.some((request) => request.url === "/api/live-agent-processes"),
    true
  );
  assert.equal(
    requests.some((request) => request.url === "/api/live-agents"),
    true
  );
});

test("session smoke button sends bounded soak controls and reports soak evidence", async () => {
  resetState();
  const { document, requests } = installHarness({
    sessionSmokePayload: {
      status: "ok",
      meeting_id: "session-smoke-generated",
      group_id: "session-smoke-generated",
      rounds_status: "answered",
      answered_round_count: 1,
      expected_reply_count: 3,
      lobby_probe_count: 1,
      reply_count: 3,
      post_restart_reply_count: 3,
      post_recover_reply_count: 3,
      soak_cycle_count: 2,
      soak_reply_count: 6,
      start_status: "ready",
      check_status: "ready",
      resume_status: "ready",
      restart_status: "ready",
      recover_status: "ready",
      stop_status: "stopped",
    },
  });
  renderLobby({ followLatest: false });
  let lobby = document.querySelector("#lobby");
  lobby.querySelector("#live-agent-session-smoke-soak-cycles").value = "2";
  lobby.querySelector("#live-agent-session-smoke-soak-interval").value = "0.5";

  renderLobby({ followLatest: false });
  lobby = document.querySelector("#lobby");
  assert.equal(lobby.querySelector("#live-agent-session-smoke-soak-cycles").value, "2");
  assert.equal(lobby.querySelector("#live-agent-session-smoke-soak-interval").value, "0.5");

  await lobby.querySelector("#live-agent-session-smoke").click();

  assert.deepEqual(sessionSmokeRequest(requests).jsonBody, {
    timeout: 12,
    soak_cycle_count: 2,
    soak_interval_seconds: 0.5,
  });
  assert.equal(
    state.liveAgentProcessStatus.message,
    "세션 smoke ok: session-smoke-generated · rounds answered (1 answered) · 3/3 replies · post-restart 3/3 replies · post-recover 3/3 replies · soak 6/6 replies over 2 cycles · start ready, check ready, resume ready, restart ready, recover ready, stop stopped"
  );
});

test("runtime refresh renders authoritative live-agent health snapshot", async () => {
  resetState();
  const { document, requests } = installHarness({
    healthPayload: {
      status: "degraded",
      agents: { total: 2, live: 1, counts: { online: 1, working: 0, error: 0, stale: 1, offline: 0 }, attention: ["agent-b"] },
      processes: { total: 2, counts: { running: 1, restarting: 0, error: 1, unknown: 0, stopped: 0 }, attention: ["resident-main"] },
      connections: { expected: 2, connected: 1, attention: ["resident-main:agent-b:stale"] },
      sessions: { total: 2, ready: 0, degraded: 2, attention: ["resident-m1:resident-main:meeting:duplicate_active_group"] },
    },
  });

  await refreshLiveAgentRuntimeSurfaces();
  renderLobby({ followLatest: false });

  const health = document.querySelector(".live-agent-runtime-health");
  assert.equal(
    requests.some((request) => request.url === "/api/live-agent-health"),
    true
  );
  assert.equal(state.liveAgentHealth.status, "degraded");
  assert.match(health.textContent, /runtime health degraded/);
  assert.match(health.textContent, /agents 1\/2 live/);
  assert.match(health.textContent, /processes 1\/2 running/);
  assert.match(health.textContent, /connections 1\/2 connected/);
  assert.match(health.textContent, /sessions 0\/2 ready/);
  assert.match(health.textContent, /attention 4/);
  assert.match(health.textContent, /session attention resident-m1:resident-main:meeting:duplicate_active_group/);
  assert.equal(health.attributes["data-tone"], "warning");
});

test("live-agent roster renders lobby and official cursors separately", () => {
  resetState();
  const { document } = installHarness({});
  state.liveAgents = [
    {
      agent_id: "agent-a",
      display_name: "Agent A",
      provider_kind: "manual",
      connection_kind: "manual",
      status: "online",
      engagement_mode: "moderator_called",
      last_observed_event_id: "lobby-evt1",
      last_observed_live_event_id: "live-evt1",
    },
  ];

  renderLobby({ followLatest: false });

  const runtime = document.querySelector(".live-agent-runtime");
  assert.match(runtime.textContent, /cursor lobby-evt1/);
  assert.match(runtime.textContent, /official cursor live-evt1/);
});

test("runtime health load failure renders unknown snapshot without crashing", async () => {
  resetState();
  const { document } = installHarness({
    healthResponse: {
      ok: false,
      status: 503,
      payload: { error: "health unavailable" },
    },
  });

  await refreshLiveAgentRuntimeSurfaces();
  renderLobby({ followLatest: false });

  const health = document.querySelector(".live-agent-runtime-health");
  assert.equal(state.liveAgentHealth.status, "unknown");
  assert.match(health.textContent, /runtime health unknown/);
  assert.equal(health.attributes["data-tone"], "error");
});

test("session smoke failure still refreshes lobby and runtime surfaces", async () => {
  resetState();
  const { document, requests } = installHarness({
    sessionSmokeResponse: {
      ok: false,
      status: 502,
      payload: { error: "session smoke failed" },
    },
  });
  renderLobby({ followLatest: false });
  const lobby = document.querySelector("#lobby");

  await lobby.querySelector("#live-agent-session-smoke").click();

  assert.equal(state.liveAgentProcessStatus.message, "상주 세션 smoke 진단 실패");
  assert.equal(state.liveAgentProcessStatus.tone, "error");
  assert.equal(
    requests.some((request) => request.url === "/api/lobby"),
    true
  );
  assert.equal(
    requests.some((request) => request.url === "/api/live-agent-processes"),
    true
  );
  assert.equal(
    requests.some((request) => request.url === "/api/live-agents"),
    true
  );
});

test("session smoke disables and guards process row actions", async () => {
  resetState();
  const { document, requests } = installHarness();
  state.liveAgentSessionSmokeRunning = true;
  state.liveAgentProcesses = [
    {
      group_id: "running-crew",
      status: "running",
      config_path: "configs/live-agents.example.json",
      server: "http://127.0.0.1:8765",
    },
    {
      group_id: "error-crew",
      status: "error",
      config_path: "configs/live-agents.example.json",
      server: "http://127.0.0.1:8765",
    },
    {
      group_id: "stopped-crew",
      status: "stopped",
      config_path: "configs/live-agents.example.json",
      server: "http://127.0.0.1:8765",
    },
  ];

  renderLobby({ followLatest: false });
  const stopButton = document.querySelectorAll("[data-live-agent-process-stop]")[0];
  const recoverButton = document.querySelector(".live-agent-process-recover");
  const restartButton = document.querySelectorAll("[data-live-agent-process-restart]")[0];

  assert.equal(stopButton.disabled, true);
  assert.equal(recoverButton.disabled, true);
  assert.equal(restartButton.disabled, true);

  await stopButton.click();
  await recoverButton.click();
  await restartButton.click();

  assert.equal(
    requests.some((request) => request.url.includes("/api/live-agent-processes/")),
    false
  );
});

test("process row action keeps the panel busy while the request is in flight", async () => {
  resetState();
  const gate = deferred();
  const { document, requests } = installHarness({ processActionGate: gate });
  state.liveAgentProcesses = [
    {
      group_id: "running-crew",
      status: "running",
      config_path: "configs/live-agents.example.json",
      server: "http://127.0.0.1:8765",
    },
  ];
  renderLobby({ followLatest: false });

  const clickPromise = document.querySelectorAll("[data-live-agent-process-stop]")[0].click();

  assert.equal(state.liveAgentProcessRowActionRunning, "running-crew");
  assert.equal(document.querySelector("#live-agent-session-smoke").disabled, true);
  await document.querySelector("#live-agent-session-smoke").click();
  assert.equal(
    requests.some((request) => request.url === "/api/live-agent-session-smoke"),
    false
  );

  gate.resolve();
  await clickPromise;

  assert.equal(state.liveAgentProcessRowActionRunning, "");
  assert.equal(
    requests.some((request) => request.url === "/api/live-agent-processes/running-crew/stop"),
    true
  );
});

test("bulk stop button posts stop-running and updates process status", async () => {
  resetState();
  const { document, requests } = installHarness({
    processStopRunningPayload: {
      result: { stopped_count: 2, failed_count: 0, skipped_count: 1, stopped: [], failed: [], skipped: [] },
      groups: [
        { group_id: "crew-a", status: "stopped" },
        { group_id: "crew-b", status: "stopped" },
      ],
    },
  });
  state.liveAgentProcesses = [
    { group_id: "crew-a", status: "running" },
    { group_id: "crew-b", status: "restarting" },
  ];

  renderLobby({ followLatest: false });
  await document.querySelector("#live-agent-process-stop-running").click();

  assert.equal(
    requests.some((request) => request.url === "/api/live-agent-processes/stop-running"),
    true
  );
  assert.equal(state.liveAgentProcesses[0].status, "stopped");
  assert.equal(state.liveAgentProcessStatus.message, "실행 그룹 2개 중지됨 · skipped 1");
});

test("official round button posts selected meeting round and requests meeting refresh", async () => {
  resetState();
  const { document, requests, events } = installHarness();
  state.payload = {
    meeting: {
      meeting_id: "resident-gui",
      meeting_template: {
        rounds: [
          { id: "round_1", title: "1라운드" },
          { id: "round_2", title: "2라운드" },
        ],
      },
      debate_rounds: [],
    },
  };
  renderLobby({ followLatest: false });
  const lobby = document.querySelector("#lobby");
  assert.equal(lobby.querySelector("#live-agent-session-meeting-id").value, "resident-gui");
  assert.equal(lobby.querySelector("#live-agent-round-id").value, "round_1");
  lobby.querySelector("#live-agent-round-timeout").value = "12";
  lobby.querySelector("#live-agent-round-stop-on-timeout").checked = true;

  await lobby.querySelector("#live-agent-call-round").click();

  assert.deepEqual(roundRequest(requests).jsonBody, {
    round_id: "round_1",
    timeout_seconds: 12,
    stop_on_timeout: true,
  });
  assert.equal(state.liveAgentProcessStatus.message, "공식 라운드 answered: round_1 · 3 answered, 0 timed out, 0 skipped");
  assert.equal(events.at(-1)?.type, "agentsassemble:meeting-refresh-requested");
  assert.equal(events.at(-1)?.detail.meetingId, "resident-gui");
});

test("official round button treats duplicate-prevented complete as success", async () => {
  resetState();
  const { document, events } = installHarness({
    roundPayload: {
      status: "complete",
      meeting_id: "resident-gui",
      round_id: "round_1",
      turn_count: 0,
      answered_count: 0,
      timeout_count: 0,
      skipped_count: 0,
    },
  });
  state.payload = {
    meeting: {
      meeting_id: "resident-gui",
      meeting_template: { rounds: [{ id: "round_1", title: "1라운드" }] },
      debate_rounds: [{ id: "round_1", status: "answered" }],
    },
  };
  renderLobby({ followLatest: false });
  const lobby = document.querySelector("#lobby");

  await lobby.querySelector("#live-agent-call-round").click();

  assert.equal(state.liveAgentProcessStatus.tone, "success");
  assert.equal(state.liveAgentProcessStatus.message, "공식 라운드 complete: round_1 · 0 answered, 0 timed out, 0 skipped");
  assert.equal(events.at(-1)?.type, "agentsassemble:meeting-refresh-requested");
});

test("official round button refuses blank round id without posting", async () => {
  resetState();
  const { document, requests } = installHarness();
  state.payload = {
    meeting: {
      meeting_id: "resident-gui",
      meeting_template: { rounds: [{ id: "round_1", title: "1라운드" }] },
      debate_rounds: [],
    },
  };
  renderLobby({ followLatest: false });
  const lobby = document.querySelector("#lobby");
  lobby.querySelector("#live-agent-round-id").value = "";

  await lobby.querySelector("#live-agent-call-round").click();

  assert.equal(roundRequest(requests), undefined);
  assert.equal(state.liveAgentProcessStatus.message, "공식 라운드 호출 실패: meeting id와 round id가 필요합니다");
});

test("official round button preserves a cleared round id across re-render", async () => {
  resetState();
  const { document, requests } = installHarness();
  state.payload = {
    meeting: {
      meeting_id: "resident-gui",
      meeting_template: { rounds: [{ id: "round_1", title: "1라운드" }] },
      debate_rounds: [],
    },
  };
  renderLobby({ followLatest: false });
  let lobby = document.querySelector("#lobby");
  lobby.querySelector("#live-agent-round-id").value = "";

  renderLobby({ followLatest: false });
  lobby = document.querySelector("#lobby");
  assert.equal(lobby.querySelector("#live-agent-round-id").value, "");

  await lobby.querySelector("#live-agent-call-round").click();

  assert.equal(roundRequest(requests), undefined);
  assert.equal(state.liveAgentProcessStatus.message, "공식 라운드 호출 실패: meeting id와 round id가 필요합니다");
});

test("official round button refuses a cleared meeting id without falling back", async () => {
  resetState();
  const { document, requests } = installHarness();
  state.payload = {
    meeting: {
      meeting_id: "resident-gui",
      meeting_template: { rounds: [{ id: "round_1", title: "1라운드" }] },
      debate_rounds: [],
    },
  };
  renderLobby({ followLatest: false });
  let lobby = document.querySelector("#lobby");
  lobby.querySelector("#live-agent-session-meeting-id").value = "";

  renderLobby({ followLatest: false });
  lobby = document.querySelector("#lobby");
  assert.equal(lobby.querySelector("#live-agent-session-meeting-id").value, "");

  await lobby.querySelector("#live-agent-call-round").click();

  assert.equal(roundRequest(requests), undefined);
  assert.equal(state.liveAgentProcessStatus.message, "공식 라운드 호출 실패: meeting id와 round id가 필요합니다");
});

test("official round timeout is clamped before posting", async () => {
  resetState();
  const { document, requests } = installHarness();
  state.payload = {
    meeting: {
      meeting_id: "resident-gui",
      meeting_template: { rounds: [{ id: "round_1", title: "1라운드" }] },
      debate_rounds: [],
    },
  };
  renderLobby({ followLatest: false });
  const lobby = document.querySelector("#lobby");
  lobby.querySelector("#live-agent-round-timeout").value = "999";

  await lobby.querySelector("#live-agent-call-round").click();

  assert.equal(roundRequest(requests).jsonBody.timeout_seconds, 600);
});

test("remaining rounds button posts bounded meeting batch and requests meeting refresh", async () => {
  resetState();
  const { document, requests, events } = installHarness();
  state.payload = {
    meeting: {
      meeting_id: "resident-gui",
      meeting_template: {
        rounds: [
          { id: "round_1", title: "1라운드" },
          { id: "round_2", title: "2라운드" },
        ],
      },
      debate_rounds: [{ id: "round_1" }],
    },
  };
  renderLobby({ followLatest: false });
  const lobby = document.querySelector("#lobby");
  lobby.querySelector("#live-agent-round-timeout").value = "12";
  lobby.querySelector("#live-agent-round-max-rounds").value = "2";
  lobby.querySelector("#live-agent-round-stop-on-timeout").checked = true;

  await lobby.querySelector("#live-agent-call-remaining-rounds").click();

  assert.deepEqual(remainingRoundsRequest(requests).jsonBody, {
    timeout_seconds: 12,
    stop_on_timeout: true,
    max_rounds: 2,
  });
  assert.equal(state.liveAgentProcessStatus.message, "남은 공식 라운드 answered: 1 rounds · 1 answered, 0 timed out, 0 skipped");
  assert.equal(events.at(-1)?.type, "agentsassemble:meeting-refresh-requested");
  assert.equal(events.at(-1)?.detail.meetingId, "resident-gui");
});

test("generated official round defaults refresh when stale draft was only the old default", () => {
  resetState();
  const { document } = installHarness();
  renderLobby({ followLatest: false });
  let lobby = document.querySelector("#lobby");
  assert.equal(lobby.querySelector("#live-agent-session-meeting-id").value, "");
  assert.equal(lobby.querySelector("#live-agent-round-id").value, "round_1");

  state.payload = {
    meeting: {
      meeting_id: "resident-gui",
      meeting_template: {
        rounds: [
          { id: "round_1", title: "1라운드" },
          { id: "round_2", title: "2라운드" },
        ],
      },
      debate_rounds: [{ id: "round_1", status: "answered" }],
    },
  };
  renderLobby({ followLatest: false });
  lobby = document.querySelector("#lobby");

  assert.equal(lobby.querySelector("#live-agent-session-meeting-id").value, "resident-gui");
  assert.equal(lobby.querySelector("#live-agent-round-id").value, "round_2");
});

test("generated official round defaults do not skip draft round records", () => {
  resetState();
  const { document } = installHarness();
  state.payload = {
    meeting: {
      meeting_id: "resident-gui",
      meeting_template: {
        rounds: [
          { id: "round_1", title: "1라운드" },
          { id: "round_2", title: "2라운드" },
        ],
      },
      debate_rounds: [{ id: "round_1", status: "draft" }],
    },
  };
  renderLobby({ followLatest: false });
  const lobby = document.querySelector("#lobby");

  assert.equal(lobby.querySelector("#live-agent-round-id").value, "round_1");
});

test("generated official round defaults treat round alias as completed", () => {
  resetState();
  const { document } = installHarness();
  state.payload = {
    meeting: {
      meeting_id: "resident-gui",
      meeting_template: {
        rounds: [
          { id: "round_1", title: "1라운드" },
          { id: "round_2", title: "2라운드" },
        ],
      },
      debate_rounds: [{ round: "round_1", status: "answered" }],
    },
  };
  renderLobby({ followLatest: false });
  const lobby = document.querySelector("#lobby");

  assert.equal(lobby.querySelector("#live-agent-round-id").value, "round_2");
});

test("generated official round defaults do not replace explicit operator values", () => {
  resetState();
  const { document } = installHarness();
  renderLobby({ followLatest: false });
  let lobby = document.querySelector("#lobby");
  lobby.querySelector("#live-agent-session-meeting-id").value = "typed-meeting";
  lobby.querySelector("#live-agent-round-id").value = "typed-round";

  state.payload = {
    meeting: {
      meeting_id: "resident-gui",
      meeting_template: { rounds: [{ id: "round_2", title: "2라운드" }] },
      debate_rounds: [],
    },
  };
  renderLobby({ followLatest: false });
  lobby = document.querySelector("#lobby");

  assert.equal(lobby.querySelector("#live-agent-session-meeting-id").value, "typed-meeting");
  assert.equal(lobby.querySelector("#live-agent-round-id").value, "typed-round");
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
      meeting_id: "resident-gui",
      config_path: "configs/live-agents.example.json",
      server: "http://127.0.0.1:8765",
      auto_restart: true,
      restart_count: 1,
      max_restarts: 3,
      restart_backoff_seconds: 5,
      stale_restart_after_seconds: 240,
      next_restart_at: "2026-05-17T12:01:00+00:00",
      recent_events: [
        {
          event_type: "stale_watchdog",
          timestamp: "2026-05-17T12:00:05+00:00",
          reason: "missing manifest agent agent-a",
        },
        {
          event_type: "restart_scheduled",
          timestamp: "2026-05-17T12:00:10+00:00",
          offline: {
            expected: 2,
            offline: 1,
            skipped: 1,
            offline_agent_ids: ["agent-a"],
            attention: [{ agent_id: "agent-b", status: "wrong_meeting" }],
          },
        },
        {
          event_type: "started",
          timestamp: "2026-05-17T12:00:20+00:00",
        },
      ],
    },
  ];

  renderLobby({ followLatest: false });

  const rowText = document.querySelector(".live-agent-process-row").textContent;
  assert.match(rowText, /meeting resident-gui/);
  assert.match(rowText, /auto restart 1\/3/);
  assert.match(rowText, /stale watchdog 240s/);
  assert.match(rowText, /next restart 2026-05-17T12:01:00\+00:00/);
  assert.match(rowText, /last event started/);
  assert.match(rowText, /last offline restart_scheduled/);
  assert.match(rowText, /last reason stale_watchdog missing manifest agent agent-a/);
  assert.match(rowText, /offline 1\/2/);
  assert.match(rowText, /wrong_meeting agent-b/);
});

test("process row recover button posts recover endpoint and updates status", async () => {
  resetState();
  const { document, requests } = installHarness();
  state.liveAgentProcesses = [
    {
      group_id: "crew",
      status: "unknown",
      pid: "",
      config_path: "configs/live-agents.example.json",
      server: "http://127.0.0.1:8765",
      recovered_from_status: "running",
    },
  ];

  renderLobby({ followLatest: false });

  await document.querySelector(".live-agent-process-recover").click();

  const recoverRequest = requests.find((request) => request.url === "/api/live-agent-processes/crew/recover");
  assert.equal(recoverRequest.options.method, "POST");
  assert.deepEqual(recoverRequest.jsonBody, {});
  assert.equal(state.liveAgentProcessStatus.message, "crew 복구됨");
  assert.equal(state.liveAgentProcesses[0].status, "running");
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

test("operation row prioritizes readiness session smoke soak statuses", () => {
  resetState();
  const { document } = installHarness();
  state.liveAgentOperations = [
    {
      timestamp: "2026-05-18T01:02:03+00:00",
      operation: "readiness.check",
      status: "success",
      target_id: "doctor-smoke",
      summary: "",
      details: {
        result_status: "ready",
        session_smoke_reply_count: 3,
        session_smoke_post_restart_reply_count: 3,
        session_smoke_post_recover_reply_count: 3,
        session_smoke_soak_cycle_count: 2,
        session_smoke_soak_reply_count: 6,
        session_smoke_soak_check_statuses: ["ready", "ready"],
        probe_statuses: ["agent-a:ok"],
      },
    },
  ];

  renderLobby({ followLatest: false });

  const rowText = document.querySelector(".live-agent-operation-row").textContent;
  assert.match(rowText, /readiness\.check/);
  assert.match(rowText, /session_smoke_post_restart_reply_count=3/);
  assert.ok(
    rowText.indexOf("session_smoke_post_restart_reply_count=3") <
      rowText.indexOf("session_smoke_post_recover_reply_count=3")
  );
  assert.match(rowText, /session_smoke_soak_check_statuses=ready,ready/);
});

test("operation row prioritizes session smoke soak evidence", () => {
  resetState();
  const { document } = installHarness();
  state.liveAgentOperations = [
    {
      timestamp: "2026-05-18T01:02:03+00:00",
      operation: "session.smoke",
      status: "success",
      target_id: "session-smoke",
      summary: "",
      details: {
        group_id: "session-smoke",
        meeting_id: "session-smoke",
        result_status: "ok",
        agent_ids: ["local", "session", "bridge"],
        rounds_status: "answered",
        round_count: 1,
        reply_count: 3,
        post_restart_reply_count: 3,
        post_recover_reply_count: 3,
        soak_cycle_count: 2,
        soak_reply_count: 6,
        soak_check_statuses: ["ready", "ready"],
      },
    },
  ];

  renderLobby({ followLatest: false });

  const rowText = document.querySelector(".live-agent-operation-row").textContent;
  assert.match(rowText, /session\.smoke/);
  assert.match(rowText, /result_status=ok/);
  assert.match(rowText, /reply_count=3/);
  assert.ok(rowText.indexOf("post_restart_reply_count=3") < rowText.indexOf("post_recover_reply_count=3"));
  assert.match(rowText, /post_recover_reply_count=3/);
  assert.match(rowText, /soak_cycle_count=2/);
  assert.match(rowText, /soak_reply_count=6/);
  assert.match(rowText, /soak_check_statuses=ready,ready/);
});

test("operation row prioritizes session control probe and auto rounds", () => {
  resetState();
  const { document } = installHarness();
  state.liveAgentOperations = [
    {
      timestamp: "2026-05-18T01:02:03+00:00",
      operation: "session.restart",
      status: "degraded",
      target_id: "council-session",
      summary: "",
      details: {
        result_status: "degraded",
        meeting_id: "main-room",
        group_id: "council",
        expected_agent_count: 3,
        connected_agent_count: 2,
        agent_ids: ["agent-a", "agent-b", "agent-c"],
        connected_agent_ids: ["agent-a", "agent-b"],
        reply_probe_status: "failed",
        reply_probe_statuses: ["agent-a:ok", "agent-b:timeout"],
        auto_rounds_status: "skipped",
        auto_rounds_reason: "probe_not_ready",
        auto_rounds_round_count: 2,
        auto_rounds_answered_round_count: 1,
      },
    },
  ];

  renderLobby({ followLatest: false });

  const rowText = document.querySelector(".live-agent-operation-row").textContent;
  assert.match(rowText, /session\.restart/);
  assert.match(rowText, /result_status=degraded/);
  assert.match(rowText, /connected_agent_count=2/);
  assert.match(rowText, /reply_probe_status=failed/);
  assert.match(rowText, /reply_probe_statuses=agent-a:ok,agent-b:timeout/);
  assert.match(rowText, /auto_rounds_status=skipped/);
  assert.match(rowText, /auto_rounds_reason=probe_not_ready/);
  assert.match(rowText, /auto_rounds_round_count=2/);
  assert.match(rowText, /auto_rounds_answered_round_count=1/);
  assert.doesNotMatch(rowText, /agent_ids=agent-a,agent-b,agent-c/);
});
