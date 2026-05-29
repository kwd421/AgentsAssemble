import assert from "node:assert/strict";
import test from "node:test";

import { state } from "../agentsassemble/static/shared.js";

class FakeClassList {
  constructor() {
    this.values = new Set();
  }

  toggle(name, enabled) {
    if (enabled) this.values.add(name);
    else this.values.delete(name);
  }

  remove(name) {
    this.values.delete(name);
  }
}

class FakeElement {
  constructor(tagName, attributes = {}, ownerDocument = null) {
    this.tagName = tagName.toUpperCase();
    this.attributes = { ...attributes };
    this.ownerDocument = ownerDocument;
    this.listeners = new Map();
    this.children = [];
    this.classList = new FakeClassList();
    this.dataset = datasetFromAttributes(attributes);
    this.id = attributes.id || "";
    this.value = attributes.value || "";
    this.textContent = "";
    this.hidden = false;
    this.disabled = false;
    this.tabIndex = 0;
    this.scrollTop = 0;
    this.scrollHeight = 100;
    this.clientHeight = 100;
    this.scope = attributes.scope || "";
    this.innerHtmlWriteCount = 0;
  }

  set innerHTML(html) {
    this._innerHTML = html;
    this.innerHtmlWriteCount += 1;
    this.children = [];
    if (this.tagName === "SELECT") return;
    this.ownerDocument?.loadInnerHtml(html, this.id || this.scope || "");
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

  append(element) {
    this.children.push(element);
  }

  insertAdjacentHTML(position, html) {
    if (position !== "beforeend") throw new Error(`Unsupported insertAdjacentHTML position: ${position}`);
    this.ownerDocument?.appendInnerHtml(String(html), this.scope);
  }

  setAttribute(name, value) {
    this.attributes[name] = String(value);
  }

  removeAttribute(name) {
    delete this.attributes[name];
    if (name === "id") this.id = "";
    if (name.startsWith("data-")) this.dataset = datasetFromAttributes(this.attributes);
    this.ownerDocument?.reindexElement(this);
  }

  remove() {
    this.ownerDocument?.removeElement(this);
  }
}

class FakeDocument {
  constructor() {
    this.documentElement = {
      style: {
        setProperty() {},
      },
    };
    this.byId = new Map();
    this.byClass = new Map();
    this.byData = new Map();
    this.scopes = new Map();
    this.tabs = ["lobby", "live", "board", "archive"].map((tab) => new FakeElement("button", { class: "tab" }, this));
    this.panels = ["lobby", "live", "board", "archive"].map((id) => new FakeElement("section", { id, class: "panel" }, this));
    this.tabs.forEach((element, index) => {
      element.dataset.tab = this.panels[index].id;
    });
    this.byClass.set("tab", this.tabs);
    this.byClass.set("panel", this.panels);
    for (const panel of this.panels) this.byId.set(panel.id, panel);
    for (const id of ["empty-state", "run-demo", "meeting-select", "meeting-subtitle", "ui-scale", "text-scale", "app-status"]) {
      this.byId.set(id, new FakeElement(id === "meeting-select" ? "select" : "div", { id }, this));
    }
  }

  querySelector(selector) {
    if (selector.startsWith("#")) return this.byId.get(selector.slice(1)) || null;
    if (selector.startsWith(".")) return this.byClass.get(selector.slice(1))?.[0] || null;
    return null;
  }

  querySelectorAll(selector) {
    if (selector.startsWith(".")) return this.byClass.get(selector.slice(1)) || [];
    if (selector.startsWith("[data-")) return this.byData.get(selector.slice(1, -1)) || [];
    return [];
  }

  createElement(tagName) {
    return new FakeElement(tagName, {}, this);
  }

  loadInnerHtml(html, scope = "") {
    if (scope) this.clearScope(scope);
    this.appendInnerHtml(html, scope);
  }

  appendInnerHtml(html, scope = "") {
    for (const match of html.matchAll(/<([a-zA-Z][\w-]*)([^>]*)>/g)) {
      const [, tagName, rawAttributes] = match;
      const attributes = parseAttributes(rawAttributes);
      const dataAttributeNames = Object.keys(attributes).filter((name) => name.startsWith("data-"));
      if (!attributes.id && !attributes.class && dataAttributeNames.length === 0) continue;
      const element = new FakeElement(tagName, attributes, this);
      element.scope = scope;
      element.textContent = elementTextContent(html, match.index + match[0].length, tagName);
      this.indexElement(element);
    }
  }

  indexElement(element) {
    if (element.id) this.byId.set(element.id, element);
    for (const className of String(element.attributes.class || "").split(/\s+/).filter(Boolean)) {
      const elements = this.byClass.get(className) || [];
      if (!elements.includes(element)) elements.push(element);
      this.byClass.set(className, elements);
    }
    for (const dataAttributeName of Object.keys(element.attributes).filter((name) => name.startsWith("data-"))) {
      const elements = this.byData.get(dataAttributeName) || [];
      if (!elements.includes(element)) elements.push(element);
      this.byData.set(dataAttributeName, elements);
    }
    if (element.scope) {
      const elements = this.scopes.get(element.scope) || new Set();
      elements.add(element);
      this.scopes.set(element.scope, elements);
    }
  }

  unindexElement(element) {
    if (element.id && this.byId.get(element.id) === element) this.byId.delete(element.id);
    for (const [className, elements] of [...this.byClass.entries()]) {
      const filtered = elements.filter((candidate) => candidate !== element);
      if (filtered.length) this.byClass.set(className, filtered);
      else this.byClass.delete(className);
    }
    for (const [attributeName, elements] of [...this.byData.entries()]) {
      const filtered = elements.filter((candidate) => candidate !== element);
      if (filtered.length) this.byData.set(attributeName, filtered);
      else this.byData.delete(attributeName);
    }
    if (element.scope) this.scopes.get(element.scope)?.delete(element);
  }

  reindexElement(element) {
    this.unindexElement(element);
    this.indexElement(element);
  }

  clearScope(scope) {
    for (const element of Array.from(this.scopes.get(scope) || [])) {
      this.unindexElement(element);
    }
    this.scopes.delete(scope);
  }

  removeElement(element) {
    this.unindexElement(element);
  }
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

function elementTextContent(html, contentStart, tagName) {
  const closeIndex = html.indexOf(`</${tagName}>`, contentStart);
  if (closeIndex < 0) return "";
  return html.slice(contentStart, closeIndex).replaceAll(/<[^>]*>/g, "").trim();
}

async function flushAsyncWork() {
  for (let index = 0; index < 40; index += 1) {
    await Promise.resolve();
  }
}

test("meeting refresh event reloads meetings and syncs the selector", async () => {
  Object.assign(state, {
    currentTab: "lobby",
    meetings: [],
    payload: null,
    lobbyEvents: [],
    lobbySignature: "[]",
    sideChatEvents: [],
    sideChatSignature: "[]",
    liveAgentsLoaded: true,
    liveAgentProcessesLoaded: true,
    liveAgentOperationsLoaded: true,
    codexSessionsLoaded: true,
  });
  const document = new FakeDocument();
  const listeners = new Map();
  const requests = [];
  globalThis.document = document;
  globalThis.localStorage = { getItem: () => "", setItem() {} };
  globalThis.requestAnimationFrame = (callback) => callback();
  globalThis.setInterval = () => 0;
  globalThis.window = {
    scrollX: 0,
    scrollY: 0,
    scrollTo(x, y) {
      this.scrollX = x;
      this.scrollY = y;
    },
    addEventListener(type, listener) {
      const items = listeners.get(type) || [];
      items.push(listener);
      listeners.set(type, items);
    },
  };
  globalThis.fetch = async (url) => {
    requests.push(String(url));
    if (url === "/api/lobby") return jsonResponse({ events: [] });
    if (url === "/api/side-chat") return jsonResponse({ events: [] });
    if (url === "/api/meetings") {
      return jsonResponse({
        meetings: [
          { meeting_id: "old-meeting", topic: "old", live_status: "running" },
          { meeting_id: "resident-gui", topic: "resident", live_status: "running" },
        ],
      });
    }
    if (url === "/api/meetings/old-meeting") return jsonResponse(meetingResponse("old-meeting"));
    if (url === "/api/meetings/resident-gui") return jsonResponse(meetingResponse("resident-gui"));
    return jsonResponse({});
  };

  await import(`../agentsassemble/static/app.js?runtime-smoke=${Date.now()}`);
  await flushAsyncWork();
  assert.equal(document.querySelector("#meeting-select").value, "");

  for (const listener of listeners.get("agentsassemble:meeting-refresh-requested") || []) {
    await listener({ detail: { meetingId: "resident-gui" } });
  }

  assert.ok(requests.includes("/api/meetings"));
  assert.ok(requests.includes("/api/meetings/resident-gui"));
  assert.equal(document.querySelector("#meeting-select").value, "resident-gui");
});

test("full meeting stream payload with only live event changes preserves the live panel shell and stable rows", async () => {
  Object.assign(state, {
    currentTab: "live",
    meetings: [],
    payload: null,
    payloadSignature: "",
    lobbyEvents: [],
    lobbySignature: "[]",
    sideChatEvents: [],
    sideChatSignature: "[]",
    liveAgentsLoaded: true,
    liveAgentProcessesLoaded: true,
    liveAgentOperationsLoaded: true,
    codexSessionsLoaded: true,
    liveAgentFlow: null,
    liveAgentFlowEvents: [],
  });
  const document = new FakeDocument();
  const requests = [];
  const eventSources = [];
  globalThis.document = document;
  globalThis.localStorage = { getItem: () => "", setItem() {} };
  globalThis.requestAnimationFrame = (callback) => callback();
  globalThis.setInterval = () => 0;
  class FakeEventSource {
    constructor(url) {
      this.url = url;
      this.listeners = new Map();
      eventSources.push(this);
    }

    addEventListener(type, listener) {
      const listeners = this.listeners.get(type) || [];
      listeners.push(listener);
      this.listeners.set(type, listeners);
    }

    close() {}
  }
  globalThis.EventSource = FakeEventSource;
  globalThis.window = {
    EventSource: FakeEventSource,
    scrollX: 0,
    scrollY: 0,
    scrollTo(x, y) {
      this.scrollX = x;
      this.scrollY = y;
    },
    addEventListener() {},
  };
  globalThis.fetch = async (url) => {
    requests.push(String(url));
    if (url === "/api/lobby") return jsonResponse({ events: [] });
    if (url === "/api/side-chat") return jsonResponse({ events: [] });
    if (url === "/api/meetings") return jsonResponse({ meetings: [{ meeting_id: "resident-gui", topic: "resident", live_status: "running" }] });
    if (url === "/api/meetings/resident-gui") return jsonResponse(meetingResponse("resident-gui", [{ id: "live-a", kind: "message", display_name: "Codex", content: "첫 발언", official_record: true }]));
    return jsonResponse({});
  };

  const unhandled = [];
  const onUnhandled = (error) => unhandled.push(error);
  process.on("unhandledRejection", onUnhandled);
  const app = await import(`../agentsassemble/static/app.js?runtime-smoke-live-refresh=${Date.now()}`);
  await flushAsyncWork();
  process.off("unhandledRejection", onUnhandled);
  assert.deepEqual(unhandled, []);
  assert.equal(state.payload?.meeting?.meeting_id, "resident-gui", `payload not loaded; requests=${requests.join(",")}`);
  const livePanel = document.querySelector("#live");
  const feed = document.querySelector(".live-transcript");
  const firstRow = document.querySelectorAll("[data-live-item-id]").find((row) => row.dataset.liveItemId === "live-a");
  assert.ok(feed, `live transcript not rendered; requests=${requests.join(",")} liveHtml=${livePanel?.innerHTML?.slice(0, 120) || ""}`);
  assert.ok(firstRow, `initial live row not rendered; liveHtml=${livePanel?.innerHTML?.slice(0, 240) || ""}`);
  feed.scrollHeight = 260;
  feed.clientHeight = 100;
  feed.scrollTop = 44;
  for (const listener of feed.listeners.get("scroll") || []) listener();
  const liveWritesBefore = livePanel.innerHtmlWriteCount;

  app.applyFullMeetingPayloadFromStream(
    meetingResponse("resident-gui", [
      { id: "live-a", kind: "message", display_name: "Codex", content: "첫 발언", official_record: true },
      { id: "live-b", kind: "message", display_name: "Kiro", content: "두 번째 발언", official_record: true },
    ])
  );
  await flushAsyncWork();

  const rows = document.querySelectorAll("[data-live-item-id]");
  assert.equal(document.querySelector("#live"), livePanel);
  assert.equal(document.querySelector(".live-transcript"), feed);
  assert.equal(livePanel.innerHtmlWriteCount, liveWritesBefore);
  assert.equal(rows.find((row) => row.dataset.liveItemId === "live-a"), firstRow);
  assert.match(rows.find((row) => row.dataset.liveItemId === "live-b")?.textContent || "", /두 번째 발언/);
  assert.equal(feed.scrollTop, 44);

  const structuralPayload = meetingResponse("resident-gui", [
    { id: "live-a", kind: "message", display_name: "Codex", content: "첫 발언", official_record: true },
    { id: "live-b", kind: "message", display_name: "Kiro", content: "두 번째 발언", official_record: true },
  ]);
  structuralPayload.meeting.topic = "retitled";
  app.applyFullMeetingPayloadFromStream(structuralPayload);
  assert.ok(livePanel.innerHtmlWriteCount > liveWritesBefore);
});

function meetingResponse(meetingId, liveEvents = []) {
  return {
    meeting: {
      meeting_id: meetingId,
      topic: meetingId,
      live_status: "running",
      roles: [],
      debate_rounds: [],
      moderator_synthesis: {},
      decision_gate: {},
    },
    live_events: liveEvents,
    artifacts: { "decision.md": `# ${meetingId}` },
    tasks: {},
    return_packets: {},
    research: {},
    research_json: {},
  };
}

function jsonResponse(payload) {
  return {
    ok: true,
    status: 200,
    json: async () => payload,
    headers: { get: () => "application/json" },
  };
}
