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

function installHarness({ readinessPayload, processStartPayload = null } = {}) {
  const requests = [];
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
    if (url === "/api/lobby") return jsonResponse({ events: [] });
    if (url === "/api/live-agents") return jsonResponse({ agents: [] });
    if (url === "/api/live-agent-processes") return jsonResponse({ groups: [] });
    if (url === "/api/live-agent-operations?limit=20") return jsonResponse({ operations: [] });
    return jsonResponse({});
  };
  return { document, requests };
}

function jsonResponse(payload) {
  return {
    ok: true,
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
