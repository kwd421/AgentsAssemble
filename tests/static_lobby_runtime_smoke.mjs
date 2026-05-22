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
    this.innerHTMLWriteCount = 0;
  }

  set innerHTML(html) {
    this.innerHTMLWriteCount += 1;
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
    liveAgentJoinBrief: null,
    liveAgentJoinBriefRunning: false,
    liveAgentProbeRunning: "",
    liveAgentHealth: null,
    liveAgentHealthLoaded: true,
    liveAgentHealthLoading: false,
    liveAgentProcesses: [],
    liveAgentProcessesLoaded: true,
    liveAgentProcessesLoading: false,
    liveAgentProcessEvents: [],
    liveAgentProcessEventsLoaded: true,
    liveAgentProcessEventsLoading: false,
    liveAgentProcessEventsMeta: null,
    liveAgentOperations: [],
    liveAgentOperationsLoaded: true,
    liveAgentOperationsLoading: false,
    liveAgentSessionRuns: [],
    liveAgentSessionRunsLoaded: true,
    liveAgentSessionRunsLoading: false,
    liveAgentSessionRunActionRunning: "",
    liveAgentSessionRunRetryNowRunning: "",
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
  sessionRunEnsurePayload = null,
  sessionRunRetryNowPayload = null,
  sessionEnsurePayload = null,
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
  codexInvitePayload = null,
  codexJoinPayload = null,
  liveAgentDiscoveryPayload = null,
  liveAgentDiscoveryResponse = null,
  liveAgentPreflightPayload = null,
  reviewCheckpointPayload = null,
  liveAgentsPayload = null,
  liveAgentJoinBriefPayload = null,
  liveAgentProcessEventsPayload = null,
  liveAgentOperationsPayload = null,
  liveAgentSessionRunsPayload = null,
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
        payloadValue(healthResponse?.payload) ||
          payloadValue(healthPayload) || {
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
    if (url === "/api/live-agent-sessions/ensure") {
      return jsonResponse(
        sessionEnsurePayload || {
          status: "ready",
          action: "resume",
          meeting_id: "resident-gui",
          group_id: "resident-main",
          connection: { expected: 3, connected: 3, attention: [] },
          process: { status: "running", attention: [] },
        }
      );
    }
    if (url === "/api/live-agent-session-runs/ensure") {
      return jsonResponse(
        sessionRunEnsurePayload || {
          status: "ready",
          action: "resume",
          meeting_id: "resident-gui",
          group_id: "resident-main",
          connection: { expected: 3, connected: 3, attention: [] },
          process: { status: "running", attention: [] },
          session_run: {
            run_id: "run-1",
            action: "ensure",
            status: "ready",
            active: true,
            meeting_id: "resident-gui",
            group_id: "resident-main",
            phase: "resume",
            reconcile_count: 0,
          },
        }
      );
    }
    const sessionRunPauseMatch = String(url).match(/^\/api\/live-agent-session-runs\/([^/]+)\/pause$/);
    if (sessionRunPauseMatch) {
      return jsonResponse({
        status: "paused",
        session_run: {
          run_id: sessionRunPauseMatch[1],
          action: "ensure",
          status: "paused",
          active: false,
          meeting_id: "resident-gui",
          group_id: "resident-main",
          phase: "paused",
          paused_status: "degraded",
        },
      });
    }
    const sessionRunResumeMatch = String(url).match(/^\/api\/live-agent-session-runs\/([^/]+)\/resume$/);
    if (sessionRunResumeMatch) {
      return jsonResponse({
        status: "resumed",
        session_run: {
          run_id: sessionRunResumeMatch[1],
          action: "ensure",
          status: "degraded",
          active: true,
          meeting_id: "resident-gui",
          group_id: "resident-main",
          phase: "resume_requested",
        },
      });
    }
    const sessionRunStopMatch = String(url).match(/^\/api\/live-agent-session-runs\/([^/]+)\/stop$/);
    if (sessionRunStopMatch) {
      return jsonResponse({
        status: "stopped",
        session_run: {
          run_id: sessionRunStopMatch[1],
          action: "ensure",
          status: "stopped",
          active: false,
          meeting_id: "resident-gui",
          group_id: "resident-main",
          phase: "stopped",
          stopped_status: "degraded",
        },
      });
    }
    const sessionRunRetryNowMatch = String(url).match(/^\/api\/live-agent-session-runs\/([^/]+)\/retry-now$/);
    if (sessionRunRetryNowMatch) {
      return jsonResponse(
        sessionRunRetryNowPayload || {
          status: "scheduled",
          session_run: {
            run_id: sessionRunRetryNowMatch[1],
            action: "ensure",
            status: "degraded",
            active: true,
            meeting_id: "resident-gui",
            group_id: "resident-main",
            phase: "retry_requested",
            reconcile_failure_count: 2,
            reconcile_backoff_seconds: 0,
            next_reconcile_at: "",
          },
          results: [],
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
    if (url === "/api/live-agent-discovery") {
      return jsonResponse(
        liveAgentDiscoveryResponse?.payload ||
          liveAgentDiscoveryPayload || {
          status: "ok",
          written: true,
          output: ".agentsassemble/live-agents.discovered.local.json",
          config: { agents: [] },
          discoveries: [],
        },
        {
          ok: liveAgentDiscoveryResponse?.ok ?? true,
          status: liveAgentDiscoveryResponse?.status ?? 200,
        }
      );
    }
    if (url === "/api/live-agent-preflight") {
      return jsonResponse(
        liveAgentPreflightPayload || {
          status: "ok",
          summary: { agents: 0 },
          agents: [],
        }
      );
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
    if (url === "/api/meetings/resident-gui/review-checkpoints") {
      return jsonResponse(
        reviewCheckpointPayload || {
          status: "answered",
          checkpoint_id: "checkpoint-gui",
          turn_count: 2,
          answered_count: 2,
          timeout_count: 0,
          skipped_count: 0,
          results: [],
        }
      );
    }
    if (url === "/api/lobby") return jsonResponse({ events: [] });
    if (url === "/api/live-agent-join-brief") {
      return jsonResponse(
        liveAgentJoinBriefPayload || {
          status: "generated",
          agent: { agent_id: "external-reviewer", display_name: "External Reviewer", meeting_id: "resident-gui" },
          commands: {
            register: ["python3", "-m", "agentsassemble.cli", "live-agent", "register", "--agent-id", "external-reviewer"],
            wait_next: ["python3", "-m", "agentsassemble.cli", "live-agent", "wait-next", "--agent-id", "external-reviewer"],
          },
          safety: { room_contacted: false, provider_executed: false, contains_secrets: false },
        }
      );
    }
    if (url === "/api/live-agents") return jsonResponse(payloadValue(liveAgentsPayload) || { agents: [] });
    if (url === "/api/live-agent-processes") return jsonResponse({ groups: [] });
    if (url === "/api/live-agent-process-events?limit=20") {
      return jsonResponse(
        liveAgentProcessEventsPayload || {
          events: [],
          limit: 20,
          group_id: "",
          scan_limit: 200,
          scanned_event_count: 0,
          truncated: false,
        }
      );
    }
    if (url === "/api/live-agent-operations?limit=20") {
      return jsonResponse(liveAgentOperationsPayload || { operations: [] });
    }
    if (url === "/api/live-agent-session-runs?limit=20&include_readiness=1") {
      return jsonResponse(liveAgentSessionRunsPayload || { runs: [] });
    }
    if (url === "/api/codex-sessions/invite") {
      return jsonResponse(
        codexInvitePayload || {
          config_path: ".agentsassemble/codex-live-session.local.json",
          binding: {
            agent_id: "codex-live-lore-lawyer",
            role_id: "lore_lawyer",
            provider_id: "codex-live",
            join_mode: "current_session",
            session_id: "019e02af-c287-7cd1-aab7-c1e059c5ed44",
          },
        }
      );
    }
    if (url === "/api/codex-sessions/join") {
      return jsonResponse(codexJoinPayload || { status: "ready", action: "resume" });
    }
    throw new Error(`Unhandled test fetch: ${url}`);
  };
  return { document, requests, events };
}

function payloadValue(value) {
  return typeof value === "function" ? value() : value;
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

function sessionEnsureRequest(requests) {
  return requests.find((request) => request.url === "/api/live-agent-sessions/ensure");
}

function sessionRunEnsureRequest(requests) {
  return requests.find((request) => request.url === "/api/live-agent-session-runs/ensure");
}

function sessionRunRetryNowRequest(requests) {
  return requests.find((request) => request.url === "/api/live-agent-session-runs/run-1/retry-now");
}

function sessionRunPauseRequest(requests) {
  return requests.find((request) => request.url === "/api/live-agent-session-runs/run-1/pause");
}

function sessionRunResumeRequest(requests) {
  return requests.find((request) => request.url === "/api/live-agent-session-runs/run-paused/resume");
}

function sessionRunStopRequest(requests) {
  return requests.find((request) => request.url === "/api/live-agent-session-runs/run-1/stop");
}

function liveAgentJoinBriefRequest(requests) {
  return requests.find((request) => request.url === "/api/live-agent-join-brief");
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

function liveAgentDiscoveryRequest(requests) {
  return requests.find((request) => request.url === "/api/live-agent-discovery");
}

function liveAgentPreflightRequest(requests) {
  return requests.find((request) => request.url === "/api/live-agent-preflight");
}

function roundRequest(requests) {
  return requests.find((request) => request.url === "/api/meetings/resident-gui/live-agent-turns/round");
}

function remainingRoundsRequest(requests) {
  return requests.find((request) => request.url === "/api/meetings/resident-gui/live-agent-turns/rounds");
}

function reviewCheckpointRequest(requests) {
  return requests.find((request) => request.url === "/api/meetings/resident-gui/review-checkpoints");
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

test("codex invite refreshes operation history after writing the invite", async () => {
  resetState();
  const { document, requests } = installHarness({
    liveAgentOperationsPayload: {
      operations: [
        {
          timestamp: "2026-05-18T01:02:03+00:00",
          operation: "codex_session.invite",
          status: "success",
          target_id: "lore_lawyer",
          summary: "wrote Codex live session invite",
          details: {
            role_id: "lore_lawyer",
            agent_id: "codex-live-lore-lawyer",
            join_mode: "current_session",
            provider_id: "codex-live",
          },
        },
      ],
    },
  });
  state.payload = {
    meeting: {
      meeting_id: "resident-gui",
      roles: [{ id: "lore_lawyer", display_name: "설정충" }],
    },
  };
  state.codexSessions = [
    {
      id: "019e02af-c287-7cd1-aab7-c1e059c5ed44",
      thread_name: "handoff",
      updated_at: "2026-05-17T00:00:00Z",
    },
  ];

  renderLobby({ followLatest: false });
  const lobby = document.querySelector("#lobby");
  lobby.querySelector("#codex-session-select").value = "019e02af-c287-7cd1-aab7-c1e059c5ed44";
  lobby.querySelector("#codex-role-select").value = "lore_lawyer";
  await lobby.querySelector("#codex-invite-form").submit();

  assert.ok(requests.some((request) => request.url === "/api/codex-sessions/invite"));
  assert.ok(requests.some((request) => request.url === "/api/live-agent-operations?limit=20"));
  const rowText = document.querySelector(".live-agent-operation-row").textContent;
  assert.match(rowText, /codex_session\.invite/);
  assert.match(rowText, /role_id=lore_lawyer/);
});

test("codex join posts the selected session and role to the resident join endpoint", async () => {
  resetState();
  const { document, requests, events } = installHarness({
    codexJoinPayload: {
      status: "ready",
      action: "resume",
      meeting_id: "resident-gui",
      group_id: "live-agents.codex-session.local",
      invite: {
        binding: {
          agent_id: "codex-live-lore-lawyer",
          role_id: "lore_lawyer",
          provider_id: "codex-live",
          join_mode: "current_session",
          session_id: "019e02af-c287-7cd1-aab7-c1e059c5ed44",
        },
      },
    },
    liveAgentOperationsPayload: {
      operations: [
        {
          timestamp: "2026-05-18T01:02:03+00:00",
          operation: "codex_session.join",
          status: "success",
          target_id: "lore_lawyer",
          summary: "joined Codex live session",
          details: {
            meeting_id: "resident-gui",
            role_id: "lore_lawyer",
            agent_id: "codex-live-lore-lawyer",
            group_id: "live-agents.codex-session.local",
            result_status: "ready",
            ensure_action: "resume",
          },
        },
      ],
    },
  });
  state.payload = {
    meeting: {
      meeting_id: "resident-gui",
      roles: [{ id: "lore_lawyer", display_name: "설정충" }],
    },
  };
  state.codexSessions = [
    {
      id: "019e02af-c287-7cd1-aab7-c1e059c5ed44",
      thread_name: "handoff",
      updated_at: "2026-05-17T00:00:00Z",
    },
  ];

  renderLobby({ followLatest: false });
  const lobby = document.querySelector("#lobby");
  lobby.querySelector("#codex-session-select").value = "019e02af-c287-7cd1-aab7-c1e059c5ed44";
  lobby.querySelector("#codex-role-select").value = "lore_lawyer";
  await lobby.querySelector("#codex-session-join").click();

  const joinRequest = requests.find((request) => request.url === "/api/codex-sessions/join");
  assert.deepEqual(joinRequest.jsonBody, {
    meeting_id: "resident-gui",
    role_id: "lore_lawyer",
    session_id: "019e02af-c287-7cd1-aab7-c1e059c5ed44",
  });
  assert.ok(requests.some((request) => request.url === "/api/live-agent-operations?limit=20"));
  assert.ok(events.some((event) => event.type === "agentsassemble:meeting-refresh-requested" && event.detail.meetingId === "resident-gui"));
  const rowText = document.querySelector(".live-agent-operation-row").textContent;
  assert.match(rowText, /codex_session\.join/);
  assert.match(rowText, /group_id=live-agents\.codex-session\.local/);
  assert.doesNotMatch(rowText, /019e02af/);
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
  assert.ok(requests.some((request) => request.url === "/api/live-agent-process-events?limit=20"));
});

test("live agent discovery writes a local config and fills the resident config path", async () => {
  resetState();
  state.payload = { meeting: { meeting_id: "resident-gui" } };
  const { document, requests } = installHarness({
    liveAgentDiscoveryPayload: {
      status: "ok",
      written: true,
      output: ".agentsassemble/live-agents.discovered.local.json",
      config: {
        agents: [
          { agent_id: "claude-local", provider_kind: "claude" },
          { agent_id: "codex-live", provider_kind: "codex_live_session" },
        ],
      },
      discoveries: [
        { provider_kind: "claude", available: true },
        { provider_kind: "codex", available: true },
      ],
      session_bundle: {
        live_agent_config_path: ".agentsassemble/live-agents.discovered.local.json",
        council_config_path: ".agentsassemble/council.discovered.local.json",
        agent_config_path: ".agentsassemble/agents.discovered.local.json",
        group_id: "live-agents.discovered.local",
      },
    },
  });

  renderLobby({ followLatest: false });
  const lobby = document.querySelector("#lobby");
  await lobby.querySelector("#live-agent-discover").click();

  assert.deepEqual(liveAgentDiscoveryRequest(requests).jsonBody, {
    meeting_id: "resident-gui",
    engagement_mode: "mentioned",
    write_config: true,
    session_bundle: true,
  });
  assert.equal(
    document.querySelector("#live-agent-process-config").value,
    ".agentsassemble/live-agents.discovered.local.json"
  );
  assert.equal(document.querySelector("#live-agent-process-group").value, "live-agents.discovered.local");
  assert.equal(document.querySelector("#live-agent-session-council-config").value, ".agentsassemble/council.discovered.local.json");
  assert.equal(document.querySelector("#live-agent-session-agent-config").value, ".agentsassemble/agents.discovered.local.json");
  assert.equal(
    state.liveAgentProcessStatus.message,
    "CLI 자동 발견 완료: 2 agents -> .agentsassemble/live-agents.discovered.local.json"
  );
  assert.ok(requests.some((request) => request.url === "/api/live-agent-operations?limit=20"));
});

test("live agent discovery renders safe candidate evidence without executable paths", async () => {
  resetState();
  state.payload = { meeting: { meeting_id: "resident-gui" } };
  const { document } = installHarness({
    liveAgentDiscoveryPayload: {
      status: "ok",
      written: true,
      output: ".agentsassemble/live-agents.discovered.local.json",
      config: {
        agents: [
          { agent_id: "claude-code-live", provider_kind: "claude_code" },
          { agent_id: "codex-live", provider_kind: "codex_live_session" },
        ],
      },
      discoveries: [
        {
          command: "claude",
          provider_kind: "claude_code",
          entry_mode: "terminal_session",
          entry_status: "ready",
          join_semantics: "terminal_pty_prompt_bridge",
          context_durability: "process_lifetime",
          evidence_basis: "path_and_pty_preflight",
          operator_action: "auto_join",
          requires_approval: true,
          safety_note: "PATH only; run preflight before auto join",
          available: true,
          included: true,
          path: "/Users/friend/secret/bin/claude",
          reason: "included",
        },
        {
          command: "codex",
          provider_kind: "codex_live_session",
          entry_mode: "codex_live_session",
          entry_status: "ready",
          join_semantics: "codex_exec_resume",
          context_durability: "provider_managed_resume",
          evidence_basis: "path_and_codex_safety_preflight",
          operator_action: "auto_join",
          requires_approval: true,
          safety_note: "Codex defaults stay centralized in preflight",
          available: true,
          included: true,
          path: "/Users/friend/secret/bin/codex",
          reason: "included",
        },
        {
          command: "gemini",
          provider_kind: "gemini_cli_legacy",
          entry_mode: "terminal_session",
          entry_status: "legacy",
          join_semantics: "terminal_pty_prompt_bridge",
          context_durability: "process_lifetime",
          evidence_basis: "path_and_pty_preflight",
          operator_action: "include_legacy_gemini",
          requires_approval: false,
          safety_note: "Legacy Gemini is skipped unless explicitly included",
          available: true,
          included: false,
          path: "/Users/friend/secret/bin/gemini",
          reason: "legacy",
        },
      ],
    },
  });

  renderLobby({ followLatest: false });
  await document.querySelector("#live-agent-discover").click();

  const reportText = document.querySelector(".live-agent-discovery-report").textContent;
  assert.match(reportText, /included 2\/3/);
  assert.match(reportText, /found 3/);
  assert.match(reportText, /claude/);
  assert.match(reportText, /claude_code/);
  assert.match(reportText, /included/);
  assert.match(reportText, /gemini/);
  assert.match(reportText, /legacy/);
  assert.match(reportText, /terminal_session/);
  assert.match(reportText, /terminal_pty_prompt_bridge/);
  assert.match(reportText, /process_lifetime/);
  assert.match(reportText, /path_and_pty_preflight/);
  assert.match(reportText, /auto_join/);
  assert.match(reportText, /approval required/);
  assert.match(reportText, /include_legacy_gemini/);
  assert.match(reportText, /codex/);
  assert.match(reportText, /codex_live_session/);
  assert.match(reportText, /codex_exec_resume/);
  assert.match(reportText, /provider_managed_resume/);
  assert.doesNotMatch(reportText, /Users|secret|bin/);
});

test("live agent discovery clears stale candidate evidence when the request fails", async () => {
  resetState();
  state.liveAgentDiscoveryReport = {
    discoveries: [
      {
        command: "claude",
        provider_kind: "claude_code",
        available: true,
        included: true,
        reason: "included",
      },
    ],
  };
  const { document } = installHarness({
    liveAgentDiscoveryResponse: {
      ok: false,
      status: 500,
      payload: { error: "discovery offline" },
    },
  });

  renderLobby({ followLatest: false });
  assert.ok(document.querySelector(".live-agent-discovery-report"));

  await document.querySelector("#live-agent-discover").click();

  assert.equal(state.liveAgentProcessStatus.message, "CLI 자동 발견 실패: discovery offline");
  assert.equal(document.querySelector(".live-agent-discovery-report"), null);
});

test("auto join discovers local CLIs preflights the generated config and records durable session run", async () => {
  resetState();
  state.payload = { meeting: { meeting_id: "resident-gui" } };
  const { document, requests, events } = installHarness({
    liveAgentDiscoveryPayload: {
      status: "ok",
      written: true,
      output: ".agentsassemble/live-agents.discovered.local.json",
      config: {
        agents: [
          { agent_id: "claude-local", provider_kind: "claude" },
          { agent_id: "codex-live", provider_kind: "codex_live_session" },
        ],
      },
      discoveries: [
        { provider_kind: "claude", available: true },
        { provider_kind: "codex", available: true },
      ],
      session_bundle: {
        live_agent_config_path: ".agentsassemble/live-agents.discovered.local.json",
        council_config_path: ".agentsassemble/council.discovered.local.json",
        agent_config_path: ".agentsassemble/agents.discovered.local.json",
        group_id: "live-agents.discovered.local",
      },
    },
    liveAgentPreflightPayload: {
      status: "ok",
      summary: { agents: 2 },
      agents: [
        { agent_id: "claude-local", status: "ok" },
        { agent_id: "codex-live", status: "ok" },
      ],
    },
    sessionRunEnsurePayload: {
      status: "ready",
      action: "start",
      meeting_id: "resident-gui",
      group_id: "resident-main",
      connection: { expected: 2, connected: 2, attention: [] },
      process: { status: "running", attention: [] },
      session_run: {
        run_id: "auto-run-1",
        action: "ensure",
        status: "ready",
        active: true,
        meeting_id: "resident-gui",
        group_id: "resident-main",
        phase: "start",
        reconcile_count: 0,
      },
    },
  });

  renderLobby({ followLatest: false });
  const lobby = document.querySelector("#lobby");
  lobby.querySelector("#live-agent-process-group").value = "stale-group";
  lobby.querySelector("#live-agent-session-council-config").value = "configs/demo-council.json";
  lobby.querySelector("#live-agent-session-agent-config").value = "configs/agents.start-session.example.json";
  lobby.querySelector("#live-agent-session-connect-timeout").value = "7";
  lobby.querySelector("#live-agent-process-auto-restart").checked = true;
  lobby.querySelector("#live-agent-process-max-restarts").value = "4";
  lobby.querySelector("#live-agent-process-restart-backoff").value = "2";
  lobby.querySelector("#live-agent-process-stale-restart-after").value = "300";

  await lobby.querySelector("#live-agent-auto-join").click();

  assert.deepEqual(liveAgentDiscoveryRequest(requests).jsonBody, {
    meeting_id: "resident-gui",
    engagement_mode: "mentioned",
    write_config: true,
    session_bundle: true,
  });
  assert.deepEqual(liveAgentPreflightRequest(requests).jsonBody, {
    config_path: ".agentsassemble/live-agents.discovered.local.json",
  });
  assert.deepEqual(sessionRunEnsureRequest(requests).jsonBody, {
    meeting_id: "resident-gui",
    group_id: "live-agents.discovered.local",
    council_config_path: ".agentsassemble/council.discovered.local.json",
    agent_config_path: ".agentsassemble/agents.discovered.local.json",
    live_agent_config_path: ".agentsassemble/live-agents.discovered.local.json",
    connect_timeout_seconds: 7,
    auto_restart: true,
    max_restarts: 4,
    restart_backoff_seconds: 2,
    stale_restart_after_seconds: 300,
  });
  assert.ok(
    requests.findIndex((request) => request.url === "/api/live-agent-discovery") <
      requests.findIndex((request) => request.url === "/api/live-agent-preflight")
  );
  assert.ok(
    requests.findIndex((request) => request.url === "/api/live-agent-preflight") <
      requests.findIndex((request) => request.url === "/api/live-agent-session-runs/ensure")
  );
  assert.equal(requests.some((request) => request.url === "/api/live-agent-sessions/ensure"), false);
  assert.equal(
    document.querySelector("#live-agent-process-config").value,
    ".agentsassemble/live-agents.discovered.local.json"
  );
  assert.equal(document.querySelector("#live-agent-process-group").value, "live-agents.discovered.local");
  assert.equal(document.querySelector("#live-agent-session-council-config").value, ".agentsassemble/council.discovered.local.json");
  assert.equal(document.querySelector("#live-agent-session-agent-config").value, ".agentsassemble/agents.discovered.local.json");
  assert.equal(state.liveAgentProcessStatus.message, "세션 ready: resident-gui · 2/2 connected · run auto-run-1 ready");
  assert.equal(events.at(-1)?.type, "agentsassemble:meeting-started");
  assert.equal(events.at(-1)?.detail.meetingId, "resident-gui");
});

test("auto join stops before preflight when discovered real providers need approval", async () => {
  resetState();
  state.payload = { meeting: { meeting_id: "resident-gui" } };
  const { document, requests } = installHarness({
    liveAgentDiscoveryPayload: {
      status: "ok",
      written: true,
      output: ".agentsassemble/live-agents.discovered.local.json",
      config: {
        agents: [
          { agent_id: "claude-local", provider_kind: "claude_code" },
          { agent_id: "codex-live", provider_kind: "codex_live_session" },
        ],
      },
      discoveries: [
        { command: "claude", provider_kind: "claude_code", available: true, included: true, requires_approval: true },
        { command: "codex", provider_kind: "codex_live_session", available: true, included: true, requires_approval: true },
      ],
      session_bundle: {
        live_agent_config_path: ".agentsassemble/live-agents.discovered.local.json",
        council_config_path: ".agentsassemble/council.discovered.local.json",
        agent_config_path: ".agentsassemble/agents.discovered.local.json",
        group_id: "live-agents.discovered.local",
      },
    },
  });

  renderLobby({ followLatest: false });
  await document.querySelector("#live-agent-auto-join").click();

  assert.equal(liveAgentDiscoveryRequest(requests).jsonBody.session_bundle, true);
  assert.equal(liveAgentPreflightRequest(requests), undefined);
  assert.equal(sessionRunEnsureRequest(requests), undefined);
  assert.equal(state.liveAgentProcessStatus.message, "자동입장 중단: 실사용 CLI 승인 필요 · claude, codex");
  assert.equal(state.liveAgentProcessStatus.tone, "error");
});

test("auto join proceeds with explicit real provider approval", async () => {
  resetState();
  state.payload = { meeting: { meeting_id: "resident-gui" } };
  const { document, requests } = installHarness({
    liveAgentDiscoveryPayload: {
      status: "ok",
      written: true,
      output: ".agentsassemble/live-agents.discovered.local.json",
      config: {
        agents: [{ agent_id: "codex-live", provider_kind: "codex_live_session" }],
      },
      discoveries: [
        { command: "codex", provider_kind: "codex_live_session", available: true, included: true, requires_approval: true },
      ],
      session_bundle: {
        live_agent_config_path: ".agentsassemble/live-agents.discovered.local.json",
        council_config_path: ".agentsassemble/council.discovered.local.json",
        agent_config_path: ".agentsassemble/agents.discovered.local.json",
        group_id: "live-agents.discovered.local",
      },
    },
    liveAgentPreflightPayload: {
      status: "ok",
      summary: { agents: 1 },
      agents: [{ agent_id: "codex-live", status: "ok" }],
    },
    sessionRunEnsurePayload: {
      status: "ready",
      action: "start",
      meeting_id: "resident-gui",
      group_id: "live-agents.discovered.local",
      connection: { expected: 1, connected: 1, attention: [] },
      process: { status: "running", attention: [] },
      session_run: {
        run_id: "auto-run-approved",
        action: "ensure",
        status: "ready",
        active: true,
        meeting_id: "resident-gui",
        group_id: "live-agents.discovered.local",
      },
    },
  });

  renderLobby({ followLatest: false });
  document.querySelector("#live-agent-auto-join-real-provider-approval").checked = true;
  await document.querySelector("#live-agent-auto-join").click();

  assert.ok(liveAgentPreflightRequest(requests));
  assert.ok(sessionRunEnsureRequest(requests));
  assert.equal(sessionRunEnsureRequest(requests).jsonBody.approve_real_providers, true);
  assert.equal(sessionRunEnsureRequest(requests).jsonBody.probe_bound_agents, true);
  assert.equal(sessionRunEnsureRequest(requests).jsonBody.probe_timeout_seconds, 12);
  assert.equal(state.liveAgentProcessStatus.message, "세션 ready: resident-gui · 1/1 connected · run auto-run-approved ready");
});

test("auto join sends selected discovery approvals before preflight and durable ensure", async () => {
  resetState();
  state.payload = { meeting: { meeting_id: "resident-gui" } };
  state.liveAgentDiscoveryReport = {
    status: "ok",
    written: true,
    output: ".agentsassemble/live-agents.discovered.local.json",
    config: {
      agents: [
        { agent_id: "claude-code-live", provider_kind: "claude_code" },
        { agent_id: "codex-live", provider_kind: "codex_live_session" },
      ],
    },
    discoveries: [
      {
        command: "claude",
        agent_id: "claude-code-live",
        provider_kind: "claude_code",
        available: true,
        included: true,
        requires_approval: true,
      },
      {
        command: "codex",
        agent_id: "codex-live",
        provider_kind: "codex_live_session",
        available: true,
        included: true,
        requires_approval: true,
      },
      {
        command: "unsafe",
        agent_id: "../unsafe live",
        provider_kind: "local_cli",
        available: true,
        included: true,
        requires_approval: true,
      },
    ],
  };
  const { document, requests } = installHarness({
    liveAgentDiscoveryPayload: {
      status: "ok",
      written: true,
      output: ".agentsassemble/live-agents.discovered.local.json",
      approval_filter: { approved_agents: ["codex-live"], approved_count: 1 },
      config: {
        agents: [{ agent_id: "codex-live", provider_kind: "codex_live_session" }],
      },
      discoveries: [
        {
          command: "claude",
          agent_id: "claude-code-live",
          provider_kind: "claude_code",
          available: true,
          included: false,
          requires_approval: true,
          approval_status: "not_approved",
        },
        {
          command: "codex",
          agent_id: "codex-live",
          provider_kind: "codex_live_session",
          available: true,
          included: true,
          requires_approval: true,
          approval_status: "approved",
        },
      ],
      session_bundle: {
        live_agent_config_path: ".agentsassemble/live-agents.discovered.local.json",
        council_config_path: ".agentsassemble/council.discovered.local.json",
        agent_config_path: ".agentsassemble/agents.discovered.local.json",
        group_id: "live-agents.discovered.local",
      },
    },
    liveAgentPreflightPayload: {
      status: "ok",
      summary: { agents: 1 },
      agents: [{ agent_id: "codex-live", status: "ok" }],
    },
    sessionRunEnsurePayload: {
      status: "ready",
      action: "start",
      meeting_id: "resident-gui",
      group_id: "live-agents.discovered.local",
      connection: { expected: 1, connected: 1, attention: [] },
      process: { status: "running", attention: [] },
      session_run: {
        run_id: "auto-run-exact",
        action: "ensure",
        status: "ready",
        active: true,
        meeting_id: "resident-gui",
        group_id: "live-agents.discovered.local",
      },
    },
  });

  renderLobby({ followLatest: false });
  assert.equal(document.querySelectorAll("[data-live-agent-discovery-approve-agent]").length, 2);
  const exactApproval = document
    .querySelectorAll("[data-live-agent-discovery-approve-agent]")
    .find((input) => input.attributes["data-live-agent-discovery-approve-agent"] === "codex-live");
  assert.ok(exactApproval);
  exactApproval.checked = true;
  renderLobby({ followLatest: false });
  const preservedApproval = document
    .querySelectorAll("[data-live-agent-discovery-approve-agent]")
    .find((input) => input.attributes["data-live-agent-discovery-approve-agent"] === "codex-live");
  assert.ok(preservedApproval);
  assert.equal(preservedApproval.checked, true);
  document.querySelector("#live-agent-auto-join-real-provider-approval").checked = true;
  await document.querySelector("#live-agent-auto-join").click();

  assert.deepEqual(liveAgentDiscoveryRequest(requests).jsonBody.approved_agents, ["codex-live"]);
  assert.equal(liveAgentDiscoveryRequest(requests).jsonBody.approved_commands, undefined);
  assert.ok(liveAgentPreflightRequest(requests));
  assert.ok(sessionRunEnsureRequest(requests));
  assert.equal(sessionRunEnsureRequest(requests).jsonBody.approve_real_providers, true);
  assert.equal(sessionRunEnsureRequest(requests).jsonBody.approved_agents, undefined);
  assert.equal(sessionRunEnsureRequest(requests).jsonBody.approved_commands, undefined);
  assert.equal(sessionRunEnsureRequest(requests).jsonBody.probe_bound_agents, true);
  assert.equal(state.liveAgentProcessStatus.message, "세션 ready: resident-gui · 1/1 connected · run auto-run-exact ready");
});

test("auto join exact approval with stale selection stops on backend approval_required", async () => {
  resetState();
  state.payload = { meeting: { meeting_id: "resident-gui" } };
  state.liveAgentDiscoveryReport = {
    status: "ok",
    discoveries: [
      {
        command: "codex",
        agent_id: "codex-live",
        provider_kind: "codex_live_session",
        available: true,
        included: true,
        requires_approval: true,
      },
    ],
  };
  const { document, requests } = installHarness({
    liveAgentDiscoveryPayload: {
      status: "approval_required",
      written: false,
      output: "",
      approval_filter: { approved_agents: [], approved_count: 0, unmatched_approval_count: 1 },
      config: { agents: [] },
      discoveries: [
        {
          command: "codex",
          agent_id: "codex-live",
          provider_kind: "codex_live_session",
          available: true,
          included: false,
          requires_approval: true,
          approval_status: "not_approved",
        },
      ],
    },
  });

  renderLobby({ followLatest: false });
  const exactApproval = document.querySelectorAll("[data-live-agent-discovery-approve-agent]")[0];
  assert.ok(exactApproval);
  exactApproval.checked = true;
  await document.querySelector("#live-agent-auto-join").click();

  assert.deepEqual(liveAgentDiscoveryRequest(requests).jsonBody.approved_agents, ["codex-live"]);
  assert.equal(liveAgentPreflightRequest(requests), undefined);
  assert.equal(sessionRunEnsureRequest(requests), undefined);
  assert.equal(state.liveAgentProcessStatus.message, "자동입장 중단: discovery approval_required · 0 agents");
  assert.equal(state.liveAgentProcessStatus.tone, "error");
});

test("join brief button generates an external agent packet without registering", async () => {
  resetState();
  state.payload = { meeting: { meeting_id: "resident-gui" } };
  const { document, requests } = installHarness({
    liveAgentJoinBriefPayload: {
      status: "generated",
      agent: {
        agent_id: "external-reviewer",
        display_name: "External Reviewer",
        provider_kind: "manual",
        connection_kind: "manual",
        meeting_id: "resident-gui",
        engagement_mode: "mentioned",
      },
      commands: {
        register: ["python3", "-m", "agentsassemble.cli", "live-agent", "register", "--agent-id", "external-reviewer"],
        wait_next: ["python3", "-m", "agentsassemble.cli", "live-agent", "wait-next", "--agent-id", "external-reviewer"],
      },
      safety: { room_contacted: false, provider_executed: false, contains_secrets: false },
      session_id: "must-not-render",
      endpoint: "https://example.invalid/private",
      config_path: "/tmp/private.json",
      unsafe_extra: { auth_ref: "secret" },
    },
  });

  renderLobby({ followLatest: false });
  const lobby = document.querySelector("#lobby");
  lobby.querySelector("#live-agent-id").value = "external-reviewer";
  lobby.querySelector("#live-agent-display-name").value = "External Reviewer";
  lobby.querySelector("#live-agent-provider-kind").value = "manual";
  lobby.querySelector("#live-agent-connection-kind").value = "manual";
  await lobby.querySelector("#live-agent-join-brief").click();

  assert.deepEqual(liveAgentJoinBriefRequest(requests).jsonBody, {
    agent_id: "external-reviewer",
    display_name: "External Reviewer",
    provider_kind: "manual",
    connection_kind: "manual",
    meeting_id: "resident-gui",
    engagement_mode: "mentioned",
    timeout: 30,
    poll_interval: 2,
    max_chain_depth: 1,
  });
  assert.equal(requests.some((request) => request.url === "/api/live-agents" && request.options.method === "POST"), false);
  assert.equal(requests.some((request) => request.url === "/api/live-agent-processes/start"), false);
  assert.equal(requests.some((request) => request.url === "/api/live-agent-sessions/start"), false);
  assert.equal(requests.some((request) => request.url === "/api/live-agent-session-runs/ensure"), false);
  assert.equal(requests.some((request) => request.url === "/api/live-agent-discovery"), false);
  assert.equal(requests.some((request) => request.url === "/api/live-agent-preflight"), false);
  assert.equal(lobby.querySelector("#live-agent-id").value, "external-reviewer");
  assert.equal(lobby.querySelector("#live-agent-display-name").value, "External Reviewer");
  assert.equal(lobby.querySelector("#live-agent-provider-kind").value, "manual");
  assert.equal(lobby.querySelector("#live-agent-connection-kind").value, "manual");
  assert.match(document.querySelector("#lobby").innerHTML, /external-reviewer/);
  assert.match(document.querySelector("#lobby").innerHTML, /wait-next/);
  assert.doesNotMatch(document.querySelector("#lobby").innerHTML, /must-not-render|example\.invalid|session_id|auth_ref|config_path|log_path/);
  assert.equal(state.liveAgentStatus.message, "external-reviewer 초대 패킷 생성됨");
  assert.equal(state.liveAgentStatus.tone, "success");
});

test("auto join stops before preflight when discovery omits the session bundle", async () => {
  resetState();
  state.payload = { meeting: { meeting_id: "resident-gui" } };
  const { document, requests } = installHarness({
    liveAgentDiscoveryPayload: {
      status: "ok",
      written: true,
      output: ".agentsassemble/live-agents.discovered.local.json",
      config: { agents: [{ agent_id: "claude-local", provider_kind: "claude" }] },
      discoveries: [{ provider_kind: "claude", available: true }],
    },
  });

  renderLobby({ followLatest: false });
  const lobby = document.querySelector("#lobby");
  lobby.querySelector("#live-agent-session-council-config").value = "configs/demo-council.json";
  lobby.querySelector("#live-agent-session-agent-config").value = "configs/agents.start-session.example.json";

  await lobby.querySelector("#live-agent-auto-join").click();

  assert.deepEqual(liveAgentDiscoveryRequest(requests).jsonBody, {
    meeting_id: "resident-gui",
    engagement_mode: "mentioned",
    write_config: true,
    session_bundle: true,
  });
  assert.equal(liveAgentPreflightRequest(requests), undefined);
  assert.equal(sessionEnsureRequest(requests), undefined);
  assert.equal(state.liveAgentProcessStatus.message, "자동입장 중단: discovery bundle 없음 · 1 agents");
  assert.equal(state.liveAgentProcessStatus.tone, "error");
  assert.equal(document.querySelector("#live-agent-session-council-config").value, "configs/demo-council.json");
  assert.equal(document.querySelector("#live-agent-session-agent-config").value, "configs/agents.start-session.example.json");
});

test("auto join stops before preflight when discovery bundle omits the live-agent config path", async () => {
  resetState();
  state.payload = { meeting: { meeting_id: "resident-gui" } };
  const { document, requests } = installHarness({
    liveAgentDiscoveryPayload: {
      status: "ok",
      written: true,
      output: ".agentsassemble/live-agents.discovered.local.json",
      config: { agents: [{ agent_id: "claude-local", provider_kind: "claude" }] },
      discoveries: [{ provider_kind: "claude", available: true }],
      session_bundle: {
        council_config_path: ".agentsassemble/council.discovered.local.json",
        agent_config_path: ".agentsassemble/agents.discovered.local.json",
        group_id: "live-agents.discovered.local",
      },
    },
  });

  renderLobby({ followLatest: false });
  await document.querySelector("#live-agent-auto-join").click();

  assert.equal(liveAgentPreflightRequest(requests), undefined);
  assert.equal(sessionEnsureRequest(requests), undefined);
  assert.equal(state.liveAgentProcessStatus.message, "자동입장 중단: discovery bundle 없음 · 1 agents");
  assert.equal(state.liveAgentProcessStatus.tone, "error");
});

test("auto join stops before ensuring the session when preflight fails", async () => {
  resetState();
  state.payload = { meeting: { meeting_id: "resident-gui" } };
  const { document, requests } = installHarness({
    liveAgentDiscoveryPayload: {
      status: "ok",
      written: true,
      output: ".agentsassemble/live-agents.discovered.local.json",
      config: { agents: [{ agent_id: "claude-local", provider_kind: "claude" }] },
      discoveries: [{ provider_kind: "claude", available: true }],
      session_bundle: {
        live_agent_config_path: ".agentsassemble/live-agents.discovered.local.json",
        council_config_path: ".agentsassemble/council.discovered.local.json",
        agent_config_path: ".agentsassemble/agents.discovered.local.json",
        group_id: "live-agents.discovered.local",
      },
    },
    liveAgentPreflightPayload: {
      status: "failed",
      summary: { agents: 1 },
      agents: [{ agent_id: "claude-local", status: "failed" }],
    },
  });

  renderLobby({ followLatest: false });
  const lobby = document.querySelector("#lobby");
  await lobby.querySelector("#live-agent-auto-join").click();

  assert.deepEqual(liveAgentPreflightRequest(requests).jsonBody, {
    config_path: ".agentsassemble/live-agents.discovered.local.json",
  });
  assert.equal(sessionEnsureRequest(requests), undefined);
  assert.equal(
    document.querySelector("#live-agent-process-config").value,
    ".agentsassemble/live-agents.discovered.local.json"
  );
  assert.equal(state.liveAgentProcessStatus.message, "자동입장 중단: preflight failed · 1 agents");
  assert.equal(state.liveAgentProcessStatus.tone, "error");
});

test("auto join is guarded while another live-agent action is busy", async () => {
  resetState();
  state.liveAgentAutoJoinRunning = true;
  const { document, requests } = installHarness();

  renderLobby({ followLatest: false });
  await document.querySelector("#live-agent-auto-join").click();

  assert.equal(liveAgentDiscoveryRequest(requests), undefined);
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

test("session ensure button posts one-shot resident session payload", async () => {
  resetState();
  const { document, requests, events } = installHarness({
    sessionEnsurePayload: {
      status: "ready",
      action: "resume",
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
  lobby.querySelector("#live-agent-session-run-remaining-rounds").checked = true;
  lobby.querySelector("#live-agent-session-probe-bound-agents").checked = true;
  lobby.querySelector("#live-agent-session-probe-timeout").value = "4";
  lobby.querySelector("#live-agent-round-timeout").value = "12";
  lobby.querySelector("#live-agent-round-max-rounds").value = "2";
  lobby.querySelector("#live-agent-round-stop-on-timeout").checked = true;

  await lobby.querySelector("#live-agent-session-ensure").click();

  assert.deepEqual(sessionEnsureRequest(requests).jsonBody, {
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
  assert.equal(state.liveAgentProcessStatus.message, "세션 ready: resident-gui · 3/3 connected");
  assert.equal(events.at(-1)?.type, "agentsassemble:meeting-started");
  assert.equal(events.at(-1)?.detail.meetingId, "resident-gui");
});

test("durable session run ensure button posts persistent resident session payload and renders run status", async () => {
  resetState();
  const { document, requests, events } = installHarness({
    sessionRunEnsurePayload: {
      status: "ready",
      action: "resume",
      meeting_id: "resident-gui",
      group_id: "resident-main",
      connection: { expected: 3, connected: 3, attention: [] },
      process: { status: "running", attention: [] },
      session_run: {
        run_id: "run-1",
        action: "ensure",
        status: "ready",
        active: true,
        meeting_id: "resident-gui",
        group_id: "resident-main",
        phase: "resume",
        reconcile_count: 1,
      },
    },
    liveAgentSessionRunsPayload: {
      runs: [
        {
          run_id: "run-1",
          action: "ensure",
          status: "ready",
          active: true,
          meeting_id: "resident-gui",
          group_id: "resident-main",
          phase: "resume",
          reconcile_count: 1,
          result: { connection: { expected: 3, connected: 3 } },
        },
      ],
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

  await lobby.querySelector("#live-agent-session-run-ensure").click();

  assert.deepEqual(sessionRunEnsureRequest(requests).jsonBody, {
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
  assert.ok(requests.some((request) => request.url === "/api/live-agent-session-runs?limit=20&include_readiness=1"));
  assert.equal(state.liveAgentProcessStatus.message, "세션 ready: resident-gui · 3/3 connected · run run-1 ready");
  assert.equal(events.at(-1)?.type, "agentsassemble:meeting-started");
  assert.equal(events.at(-1)?.detail.meetingId, "resident-gui");
  const runText = document.querySelector(".live-agent-session-run-row").textContent;
  assert.match(runText, /run-1/);
  assert.match(runText, /resident-gui/);
  assert.match(runText, /resident-main/);
  assert.match(runText, /ready/);
  assert.match(runText, /connected 3\/3/);
  assert.doesNotMatch(runText, /configs\/|http:|https:/);
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
      session_runs: [
        {
          run_id: "run-stop-1",
          status: "stopped",
          active: false,
          meeting_id: "resident-gui",
          group_id: "resident-main",
        },
      ],
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
    "세션 stopped: resident-gui · resident-main · 3/3 offline · runs stopped 1"
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
      process_monitor: {
        running: true,
        interval_seconds: 2.5,
        last_tick_at: "2026-05-21T10:09:00+00:00",
        last_status: "ok",
        last_group_count: 2,
        last_error_type: "",
      },
      connections: { expected: 2, connected: 1, attention: ["resident-main:agent-b:stale"] },
      sandbox_enforcement: {
        counts: { advisory: 1, codex_readonly: 1, os_sandboxed: 0, unknown: 0 },
        attention: ["unknown-agent"],
      },
      sessions: { total: 2, ready: 0, degraded: 2, attention: ["resident-m1:resident-main:meeting:duplicate_active_group"] },
      session_runs: {
        total: 2,
        active: 1,
        ready: 1,
        retrying: 1,
        attention: ["resident-m1:resident-main:run-1:degraded:retrying"],
        items: [
          {
            run_id: "run-1",
            meeting_id: "resident-m1",
            group_id: "resident-main",
            status: "degraded",
            reconcile_failure_count: 2,
            reconcile_backoff_seconds: 120,
            next_reconcile_at: "2026-05-21T10:07:00+00:00",
          },
        ],
      },
      session_run_monitor: {
        running: true,
        interval_seconds: 2.5,
        last_tick_at: "2026-05-21T10:08:00+00:00",
        last_status: "ok",
        last_result_count: 1,
        last_error_type: "",
      },
      observations: {
        ready_agent_count: 2,
        lobby_behind_count: 1,
        live_behind_count: 0,
        error_count: 0,
        latest_lobby_event_id: "lobby-7",
        latest_live_request_count: 0,
        attention: ["resident-m1:resident-main:agent-b:lobby_cursor_behind"],
      },
      admission: {
        total: 3,
        host_approved: 1,
        unapproved: 2,
        counts: {
          bound_to_meeting: 1,
          binding_conflict: 1,
          meeting_lobby_only: 1,
          meeting_missing: 0,
          lobby_only: 0,
          unknown: 0,
        },
        attention: [
          "resident-m1:agent-b:binding_conflict",
          "resident-m1:guest-agent:meeting_lobby_only",
        ],
      },
      shared_memory: {
        ready_sessions: 1,
        with_memory: 1,
        official_event_count: 2,
        open_question_count: 1,
        action_item_count: 1,
        last_official_event_id: "reply-2",
        attention: [],
      },
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
  assert.match(health.textContent, /process monitor running/);
  assert.match(health.textContent, /groups 2/);
  assert.match(health.textContent, /connections 1\/2 connected/);
  assert.match(health.textContent, /sessions 0\/2 ready/);
  assert.match(health.textContent, /session-runs 1\/2 active/);
  assert.match(health.textContent, /attention 7/);
  assert.match(health.textContent, /sandbox advisory 1/);
  assert.match(health.textContent, /codex_readonly 1/);
  assert.match(health.textContent, /sandbox attention unknown-agent/);
  assert.match(health.textContent, /observations 2 ready agents/);
  assert.match(health.textContent, /lobby behind 1/);
  assert.match(health.textContent, /admission 1\/3 host-approved/);
  assert.match(health.textContent, /binding conflict 1/);
  assert.match(health.textContent, /meeting lobby 1/);
  assert.match(health.textContent, /admission attention resident-m1:agent-b:binding_conflict/);
  assert.match(health.textContent, /shared memory 2 official events/);
  assert.match(health.textContent, /1\/1 ready sessions/);
  assert.match(health.textContent, /questions 1/);
  assert.match(health.textContent, /actions 1/);
  assert.match(health.textContent, /last reply-2/);
  assert.match(health.textContent, /observation attention resident-m1:resident-main:agent-b:lobby_cursor_behind/);
  assert.match(health.textContent, /retry failures 2/);
  assert.match(health.textContent, /retry backoff 120s/);
  assert.match(health.textContent, /next retry 2026-05-21T10:07:00\+00:00/);
  assert.match(health.textContent, /session-run monitor running/);
  assert.match(health.textContent, /interval 2\.5s/);
  assert.match(health.textContent, /last tick 2026-05-21T10:08:00\+00:00/);
  assert.match(health.textContent, /last ok/);
  assert.match(health.textContent, /session attention resident-m1:resident-main:meeting:duplicate_active_group/);
  assert.match(health.textContent, /session-run attention resident-m1:resident-main:run-1:degraded:retrying/);
  assert.equal(health.attributes["data-tone"], "warning");
});

test("runtime refresh does not re-render for volatile heartbeat age and monitor ticks only", async () => {
  resetState();
  let agentAge = 0;
  let healthTick = 0;
  const { document } = installHarness({
    liveAgentsPayload: () => ({
      agents: [
        {
          agent_id: "agent-a",
          display_name: "Agent A",
          status: "online",
          heartbeat_age_seconds: (agentAge += 5),
          stale_after_seconds: 30,
          provider_kind: "local_cli",
          connection_kind: "local_cli",
        },
      ],
    }),
    healthPayload: () => ({
      status: "ok",
      agents: {
        total: 1,
        live: 1,
        counts: { online: 1, working: 0, error: 0, stale: 0, offline: 0 },
        attention: [],
      },
      processes: {
        total: 0,
        counts: { running: 0, restarting: 0, error: 0, unknown: 0, stopped: 0 },
        attention: [],
      },
      connections: { expected: 1, connected: 1, attention: [] },
      process_monitor: {
        running: true,
        interval_seconds: 5,
        last_status: "ok",
        last_tick_at: `2026-05-21T10:00:0${++healthTick}+00:00`,
        last_group_count: 0,
      },
      session_run_monitor: {
        running: true,
        interval_seconds: 5,
        last_status: "ok",
        last_tick_at: `2026-05-21T10:00:0${healthTick}+00:00`,
        last_result_count: 0,
      },
    }),
  });

  await refreshLiveAgentRuntimeSurfaces();
  const renderCountAfterInitialLoad = document.lobby.innerHTMLWriteCount;

  await refreshLiveAgentRuntimeSurfaces();

  assert.equal(state.liveAgents[0].heartbeat_age_seconds, 10);
  assert.equal(state.liveAgentHealth.process_monitor.last_tick_at, "2026-05-21T10:00:02+00:00");
  assert.equal(document.lobby.innerHTMLWriteCount, renderCountAfterInitialLoad);
});

test("runtime health renders meeting-owned session readiness details", async () => {
  resetState();
  const { document } = installHarness({
    healthPayload: {
      status: "degraded",
      agents: { total: 2, live: 1, counts: { online: 1, working: 0, error: 0, stale: 1, offline: 0 }, attention: [] },
      processes: {
        total: 1,
        counts: { running: 1, restarting: 0, error: 0, unknown: 0, stopped: 0 },
        attention: [],
        reasons: {
          "resident-main": {
            event_type: "stale_watchdog",
            reason: "stale manifest agent agent-b",
          },
        },
      },
      connections: { expected: 2, connected: 1, attention: ["resident-main:agent-b:stale"] },
      sessions: {
        total: 1,
        ready: 0,
        degraded: 1,
        attention: ["resident-m1:resident-main:agent-b:stale"],
        items: [
          {
            meeting_id: "resident-m1",
            group_id: "resident-main",
            status: "degraded",
            process_status: "running",
            expected: 2,
            connected: 1,
            ownership_attention: ["meeting:duplicate_active_group"],
            process_attention: ["group:running"],
            connection_attention: ["agent-b:stale"],
            attention: ["meeting:duplicate_active_group", "agent-b:stale"],
            process_reason: {
              event_type: "stale_watchdog",
              reason: "stale manifest agent agent-b",
            },
          },
        ],
      },
    },
  });

  await refreshLiveAgentRuntimeSurfaces();
  renderLobby({ followLatest: false });

  const rowText = document.querySelector(".live-agent-session-row").textContent;
  assert.match(rowText, /resident-m1/);
  assert.match(rowText, /resident-main/);
  assert.match(rowText, /degraded/);
  assert.match(rowText, /process running/);
  assert.match(rowText, /connected 1\/2/);
  assert.match(rowText, /ownership meeting:duplicate_active_group/);
  assert.match(rowText, /process group:running/);
  assert.match(rowText, /connection agent-b:stale/);
  assert.match(rowText, /reason stale_watchdog stale manifest agent agent-b/);
});

test("runtime refresh loads durable session runs and renders current readiness evidence", async () => {
  resetState();
  const { document, requests } = installHarness({
    liveAgentSessionRunsPayload: {
      runs: [
        {
          run_id: "run-2",
          action: "ensure",
          status: "ready",
          active: true,
          meeting_id: "resident-gui",
          group_id: "resident-main",
          phase: "recover",
          reconcile_count: 2,
          reconcile_failure_count: 2,
          reconcile_backoff_seconds: 120,
          next_reconcile_at: "2026-05-21T10:07:00+00:00",
          request: {
            live_agent_config_path: "configs/private.json",
            server: "https://secret.example",
            auth_ref: "env:SECRET_TOKEN",
            command: ["provider", "--token", "secret"],
            prompt: "private prompt",
          },
          result: {
            connection: { expected: 3, connected: 3 },
            provider_output: "private provider output",
            log_tail: "private log tail",
          },
          readiness: {
            status: "degraded",
            expected: 3,
            connected: 1,
            connection_attention: ["agent-c:offline"],
          },
        },
      ],
    },
  });

  await refreshLiveAgentRuntimeSurfaces();
  renderLobby({ followLatest: false });

  assert.ok(requests.some((request) => request.url === "/api/live-agent-session-runs?limit=20&include_readiness=1"));
  const runRow = document.querySelector(".live-agent-session-run-row");
  const runText = runRow.textContent;
  assert.match(runText, /run-2/);
  assert.match(runText, /resident-gui/);
  assert.match(runText, /resident-main/);
  assert.match(runText, /ready/);
  assert.match(runText, /readiness degraded · run ready · active/);
  assert.match(runText, /stored connected 3\/3/);
  assert.match(runText, /readiness degraded/);
  assert.match(runText, /current connected 1\/3/);
  assert.match(runText, /connection agent-c:offline/);
  assert.match(runText, /reconcile 2/);
  assert.match(runText, /retry failures 2/);
  assert.match(runText, /retry backoff 120s/);
  assert.match(runText, /next retry 2026-05-21T10:07:00\+00:00/);
  assert.doesNotMatch(runText, /configs\/|secret\.example|https:|SECRET_TOKEN|--token|private prompt|provider output|log tail/);
});

test("session run retry button schedules immediate durable retry and refreshes run list", async () => {
  resetState();
  const { document, requests } = installHarness({
    liveAgentSessionRunsPayload: {
      runs: [
        {
          run_id: "run-1",
          action: "ensure",
          status: "degraded",
          active: true,
          meeting_id: "resident-gui",
          group_id: "resident-main",
          phase: "reconcile_failed",
          reconcile_count: 3,
          reconcile_failure_count: 2,
          reconcile_backoff_seconds: 120,
          next_reconcile_at: "2026-05-21T10:07:00+00:00",
        },
      ],
    },
  });
  renderLobby({ followLatest: false });
  await refreshLiveAgentRuntimeSurfaces();

  const retryButton = document.querySelectorAll("[data-live-agent-session-run-retry-now]")[0];
  assert.ok(retryButton);

  await retryButton.click();

  assert.deepEqual(sessionRunRetryNowRequest(requests).jsonBody, {});
  assert.ok(requests.some((request) => request.url === "/api/live-agent-session-runs?limit=20&include_readiness=1"));
  assert.equal(state.liveAgentProcessStatus.message, "run-1 재시도 예약됨");
});

test("session run retry button is hidden for ready runs with ready current readiness", async () => {
  resetState();
  const { document } = installHarness({
    liveAgentSessionRunsPayload: {
      runs: [
        {
          run_id: "run-ready",
          action: "ensure",
          status: "ready",
          active: true,
          meeting_id: "resident-gui",
          group_id: "resident-main",
          phase: "none",
          readiness: {
            status: "ready",
            expected: 3,
            connected: 3,
          },
        },
      ],
    },
  });
  renderLobby({ followLatest: false });
  await refreshLiveAgentRuntimeSurfaces();

  assert.equal(document.querySelectorAll("[data-live-agent-session-run-retry-now]").length, 0);
});

test("session run pause and resume buttons control durable automation", async () => {
  resetState();
  const { document, requests } = installHarness({
    liveAgentSessionRunsPayload: {
      runs: [
        {
          run_id: "run-1",
          action: "ensure",
          status: "degraded",
          active: true,
          meeting_id: "resident-gui",
          group_id: "resident-main",
          phase: "reconcile_failed",
          reconcile_failure_count: 1,
          reconcile_backoff_seconds: 60,
        },
        {
          run_id: "run-paused",
          action: "ensure",
          status: "paused",
          active: false,
          meeting_id: "resident-gui",
          group_id: "resident-main",
          phase: "paused",
          paused_status: "degraded",
        },
      ],
    },
  });
  renderLobby({ followLatest: false });
  await refreshLiveAgentRuntimeSurfaces();

  const pauseButton = document.querySelectorAll("[data-live-agent-session-run-pause]")[0];
  assert.ok(pauseButton);
  await pauseButton.click();

  assert.deepEqual(sessionRunPauseRequest(requests).jsonBody, {});
  assert.equal(state.liveAgentProcessStatus.message, "run-1 일시정지됨");

  renderLobby({ followLatest: false });
  const resumeButton = document.querySelectorAll("[data-live-agent-session-run-resume]")[0];
  assert.ok(resumeButton);
  await resumeButton.click();

  assert.deepEqual(sessionRunResumeRequest(requests).jsonBody, {});
  assert.equal(state.liveAgentProcessStatus.message, "run-paused 재개됨");
});

test("session run stop button stops one durable automation intent", async () => {
  resetState();
  const { document, requests } = installHarness({
    liveAgentSessionRunsPayload: {
      runs: [
        {
          run_id: "run-1",
          action: "ensure",
          status: "degraded",
          active: true,
          meeting_id: "resident-gui",
          group_id: "resident-main",
          phase: "reconcile_failed",
          reconcile_failure_count: 1,
          reconcile_backoff_seconds: 60,
        },
      ],
    },
  });
  renderLobby({ followLatest: false });
  await refreshLiveAgentRuntimeSurfaces();

  const stopButton = document.querySelectorAll("[data-live-agent-session-run-stop]")[0];
  assert.ok(stopButton);
  await stopButton.click();

  assert.deepEqual(sessionRunStopRequest(requests).jsonBody, {});
  assert.equal(state.liveAgentProcessStatus.message, "run-1 중지됨");
});

test("runtime health session readiness renders only safe escaped evidence", async () => {
  resetState();
  const { document } = installHarness({
    healthPayload: {
      status: "degraded",
      agents: { total: 1, live: 0, counts: {}, attention: [] },
      processes: { total: 1, counts: { running: 0 }, attention: [] },
      connections: { expected: 1, connected: 0, attention: [] },
      sessions: {
        total: 1,
        ready: 0,
        items: [
          {
            meeting_id: "resident-<script>alert(1)</script>",
            group_id: "resident-main",
            status: "degraded",
            process_status: "error",
            expected: 1,
            connected: 0,
            ownership_attention: ["meeting:<img src=x onerror=alert(1)>"],
            process_attention: ["group:error"],
            connection_attention: ["agent-a:offline"],
            process_reason: {
              event_type: "failed_start",
              reason: "provider <b>offline</b>",
            },
            command: ["claude", "--danger"],
            endpoint: "https://example.invalid/private",
            auth_ref: "env:SECRET",
            prompt: "hidden prompt",
            log_tail: "hidden log",
          },
        ],
      },
    },
  });

  await refreshLiveAgentRuntimeSurfaces();
  renderLobby({ followLatest: false });

  const rowText = document.querySelector(".live-agent-session-row").textContent;
  assert.match(rowText, /resident-<script>alert\(1\)<\/script>/);
  assert.match(rowText, /meeting:<img src=x onerror=alert\(1\)>/);
  assert.match(rowText, /provider <b>offline<\/b>/);
  assert.doesNotMatch(document.querySelector("#lobby").innerHTML, /<script>|<img|<b>/);
  assert.doesNotMatch(rowText, /--danger|example\.invalid|env:SECRET|hidden prompt|hidden log/);
});

test("runtime refresh renders recent lifecycle events with reason and offline evidence", async () => {
  resetState();
  const { document, requests } = installHarness({
    liveAgentProcessEventsPayload: {
      events: [
        {
          timestamp: "2026-05-20T01:02:03+00:00",
          group_id: "resident-main",
          event_type: "stale_watchdog",
          status: "restarting",
          pid: 1234,
          returncode: 97,
          restart_count: 1,
          max_restarts: 3,
          reason: "stale manifest agent claude-local",
          offline: {
            expected: 2,
            offline: 1,
            attention: [{ agent_id: "claude-local", status: "stale" }],
          },
        },
      ],
      limit: 20,
      group_id: "",
      scan_limit: 20,
      scanned_event_count: 20,
      truncated: true,
    },
  });

  renderLobby({ followLatest: false });
  await refreshLiveAgentRuntimeSurfaces();

  assert.ok(requests.some((request) => request.url === "/api/live-agent-process-events?limit=20"));
  const rowText = document.querySelector(".live-agent-lifecycle-row").textContent;
  assert.match(rowText, /resident-main/);
  assert.match(rowText, /stale_watchdog/);
  assert.match(rowText, /2026-05-20T01:02:03\+00:00/);
  assert.match(rowText, /restarting/);
  assert.match(rowText, /pid 1234/);
  assert.match(rowText, /returncode 97/);
  assert.match(rowText, /restart 1\/3/);
  assert.match(rowText, /reason stale manifest agent claude-local/);
  assert.match(rowText, /offline 1\/2/);
  assert.match(rowText, /stale claude-local/);
  assert.match(document.querySelector(".live-agent-lifecycle-meta").textContent, /searched recent 20 lifecycle events/);
});

test("runtime refresh renders every lifecycle event returned by the bounded query", async () => {
  resetState();
  const events = Array.from({ length: 8 }, (_, index) => ({
    timestamp: `2026-05-20T01:0${index}:00+00:00`,
    group_id: `crew-${index}`,
    event_type: index === 0 ? "started" : "updated",
    status: "running",
    pid: 2000 + index,
  }));
  const { document } = installHarness({
    liveAgentProcessEventsPayload: {
      events,
      limit: 20,
      group_id: "",
      scan_limit: 20,
      scanned_event_count: 8,
      truncated: false,
    },
  });

  renderLobby({ followLatest: false });
  await refreshLiveAgentRuntimeSurfaces();

  assert.equal(document.querySelectorAll(".live-agent-lifecycle-row").length, 8);
});

test("process panel refresh reloads lifecycle history", async () => {
  resetState();
  const { document, requests } = installHarness();

  renderLobby({ followLatest: false });
  await document.querySelector("#live-agent-process-refresh").click();

  assert.ok(requests.some((request) => request.url === "/api/live-agent-process-events?limit=20"));
  assert.ok(requests.some((request) => request.url === "/api/live-agent-session-runs?limit=20&include_readiness=1"));
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
  assert.equal(document.querySelector("#live-agent-session-run-ensure").disabled, true);
  await document.querySelector("#live-agent-session-smoke").click();
  await document.querySelector("#live-agent-session-run-ensure").click();
  assert.equal(
    requests.some((request) => request.url === "/api/live-agent-session-smoke"),
    false
  );
  assert.equal(
    requests.some((request) => request.url === "/api/live-agent-session-runs/ensure"),
    false
  );

  gate.resolve();
  await clickPromise;

  assert.equal(state.liveAgentProcessRowActionRunning, "");
  assert.equal(
    requests.some((request) => request.url === "/api/live-agent-processes/running-crew/stop"),
    true
  );
  assert.equal(
    requests.some((request) => request.url === "/api/live-agent-process-events?limit=20"),
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
  assert.equal(
    requests.some((request) => request.url === "/api/live-agent-process-events?limit=20"),
    true
  );
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

test("review checkpoint button posts resident review request and refreshes operations", async () => {
  resetState();
  const { document, requests, events } = installHarness();
  state.payload = { meeting: { meeting_id: "resident-gui" } };
  renderLobby({ followLatest: false });
  const lobby = document.querySelector("#lobby");
  lobby.querySelector("#live-agent-process-group").value = "resident-main";
  lobby.querySelector("#live-agent-review-checkpoint-message").value = "Review this slice before commit.";
  lobby.querySelector("#live-agent-review-checkpoint-id").value = "checkpoint-gui";
  lobby.querySelector("#live-agent-review-checkpoint-timeout").value = "8";

  await lobby.querySelector("#live-agent-review-checkpoint").click();

  assert.deepEqual(reviewCheckpointRequest(requests).jsonBody, {
    group_id: "resident-main",
    content: "Review this slice before commit.",
    checkpoint_id: "checkpoint-gui",
    timeout_seconds: 8,
  });
  assert.equal(
    requests.some((request) => request.url === "/api/live-agent-operations?limit=20"),
    true
  );
  assert.equal(events.at(-1)?.type, "agentsassemble:meeting-refresh-requested");
  assert.equal(events.at(-1)?.detail.meetingId, "resident-gui");
  assert.equal(state.liveAgentProcessStatus.message, "리뷰 checkpoint answered: checkpoint-gui · 2/2 answered, 0 timed out, 0 skipped");
});

test("review checkpoint message enter posts resident review request instead of starting process", async () => {
  resetState();
  const { document, requests } = installHarness();
  state.payload = { meeting: { meeting_id: "resident-gui" } };
  renderLobby({ followLatest: false });
  const lobby = document.querySelector("#lobby");
  lobby.querySelector("#live-agent-process-group").value = "resident-main";
  const messageInput = lobby.querySelector("#live-agent-review-checkpoint-message");
  messageInput.value = "Review from keyboard.";

  let prevented = false;
  for (const listener of messageInput.listeners.get("keydown") || []) {
    await listener({
      key: "Enter",
      isComposing: false,
      preventDefault() {
        prevented = true;
      },
    });
  }

  assert.equal(prevented, true);
  assert.equal(processStartRequest(requests), undefined);
  assert.equal(reviewCheckpointRequest(requests).jsonBody.content, "Review from keyboard.");
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
  assert.equal(
    requests.some((request) => request.url === "/api/live-agent-process-events?limit=20"),
    true
  );
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

test("operation row prioritizes review checkpoint evidence", () => {
  resetState();
  const { document } = installHarness();
  state.liveAgentOperations = [
    {
      timestamp: "2026-05-18T01:02:03+00:00",
      operation: "review.checkpoint",
      status: "success",
      target_id: "m1",
      summary: "",
      details: {
        meeting_id: "m1",
        group_id: "resident-main",
        checkpoint_id: "checkpoint-1",
        result_status: "answered",
        turn_count: 2,
        answered_count: 2,
        timeout_count: 0,
        skipped_count: 0,
        agent_ids: ["agent-a", "agent-b"],
        statuses: ["answered", "answered"],
        request_event_ids: ["request-a", "request-b"],
        reply_event_ids: ["reply-a", "reply-b"],
      },
    },
  ];

  renderLobby({ followLatest: false });

  const rowText = document.querySelector(".live-agent-operation-row").textContent;
  assert.match(rowText, /review\.checkpoint/);
  assert.match(rowText, /result_status=answered/);
  assert.match(rowText, /checkpoint_id=checkpoint-1/);
  assert.match(rowText, /answered_count=2/);
  assert.match(rowText, /timeout_count=0/);
  assert.ok(rowText.indexOf("result_status=answered") < rowText.indexOf("checkpoint_id=checkpoint-1"));
  assert.doesNotMatch(rowText, /request_event_ids=/);
});

test("operation row prioritizes discovery exact approval evidence", () => {
  resetState();
  const { document } = installHarness();
  state.liveAgentOperations = [
    {
      timestamp: "2026-05-18T01:02:03+00:00",
      operation: "discovery.run",
      status: "success",
      target_id: "live-agent-discovery",
      summary: "",
      details: {
        agents: 1,
        discovered: 3,
        join_semantics: ["terminal_pty_prompt_bridge", "codex_exec_resume"],
        context_durability: ["process_lifetime", "provider_managed_resume"],
        evidence_basis: ["path_and_pty_preflight", "path_and_codex_safety_preflight"],
        approval_required: 1,
        result_status: "ok",
        approved_count: 1,
        approved_agent_ids: ["codex-live"],
        excluded_agent_count: 2,
        unmatched_approval_count: 1,
      },
    },
  ];

  renderLobby({ followLatest: false });

  const rowText = document.querySelector(".live-agent-operation-row").textContent;
  assert.match(rowText, /discovery\.run/);
  assert.match(rowText, /result_status=ok/);
  assert.match(rowText, /approved_count=1/);
  assert.match(rowText, /approved_agent_ids=codex-live/);
  assert.match(rowText, /excluded_agent_count=2/);
  assert.match(rowText, /unmatched_approval_count=1/);
  assert.ok(rowText.indexOf("approved_count=1") < rowText.indexOf("agents=1"));
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

test("operation row prioritizes readiness health reasons before smoke details", () => {
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
        session_smoke_reply_count: 3,
        session_smoke_post_restart_reply_count: 3,
        health_process_attention: ["orphan-group"],
        health_process_reasons: ["orphan-group recovered_unknown orphan running record marked unknown"],
        health_session_attention: ["resident-m1:process"],
        probe_statuses: ["agent-a:ok"],
      },
    },
  ];

  renderLobby({ followLatest: false });

  const rowText = document.querySelector(".live-agent-operation-row").textContent;
  assert.match(rowText, /health_process_reasons=orphan-group recovered_unknown orphan running record marked unknown/);
  assert.match(rowText, /health_process_attention=orphan-group/);
  assert.ok(
    rowText.indexOf("health_process_reasons=") <
      rowText.indexOf("session_smoke_reply_count=3")
  );
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
