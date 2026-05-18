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
  }

  set innerHTML(html) {
    this._innerHTML = html;
    this.children = [];
    if (this.tagName === "SELECT") return;
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

  append(element) {
    this.children.push(element);
  }

  setAttribute(name, value) {
    this.attributes[name] = String(value);
  }

  removeAttribute(name) {
    delete this.attributes[name];
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
    if (selector.startsWith("[data-")) return [];
    return [];
  }

  createElement(tagName) {
    return new FakeElement(tagName, {}, this);
  }

  loadInnerHtml(html) {
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
  for (let index = 0; index < 8; index += 1) {
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

function meetingResponse(meetingId) {
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
    live_events: [],
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
