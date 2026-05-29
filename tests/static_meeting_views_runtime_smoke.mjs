import assert from "node:assert/strict";
import test from "node:test";

import { refreshLiveTranscript, renderLive } from "../agentsassemble/static/meeting-views.js";
import { state } from "../agentsassemble/static/shared.js";

class FakeElement {
  constructor(tagName, attributes = {}, ownerDocument = null) {
    this.tagName = tagName.toUpperCase();
    this.attributes = { ...attributes };
    this.ownerDocument = ownerDocument;
    this.listeners = new Map();
    this.id = attributes.id || "";
    this.dataset = datasetFromAttributes(attributes);
    this.textContent = "";
    this.value = attributes.value || "";
    this.hidden = false;
    this.scrollTop = 0;
    this.scrollHeight = 100;
    this.clientHeight = 100;
  }

  set innerHTML(html) {
    this._innerHTML = String(html);
    this.textContent = textFromHtml(this._innerHTML);
    if (this.id === "live") this.ownerDocument?.loadInnerHtml(this._innerHTML);
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

  insertAdjacentHTML(position, html) {
    if (position !== "beforeend") throw new Error(`Unsupported insertAdjacentHTML position: ${position}`);
    this.ownerDocument?.appendInnerHtml(String(html));
  }

  setAttribute(name, value) {
    this.ownerDocument?.updateElementAttribute(this, name, String(value));
  }

  removeAttribute(name) {
    this.ownerDocument?.removeElementAttribute(this, name);
  }

  remove() {
    this.ownerDocument?.removeElement(this);
  }
}

class FakeDocument {
  constructor() {
    this.activeElement = null;
    this.live = new FakeElement("section", { id: "live" }, this);
    this.byId = new Map([["live", this.live]]);
    this.byClass = new Map();
    this.byData = new Map();
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

  loadInnerHtml(html) {
    this.byId = new Map([["live", this.live]]);
    this.byClass = new Map();
    this.byData = new Map();
    this.appendInnerHtml(html);
  }

  appendInnerHtml(html) {
    for (const match of html.matchAll(/<([a-zA-Z][\w-]*)([^>]*)>/g)) {
      const [, tagName, rawAttributes] = match;
      const attributes = parseAttributes(rawAttributes);
      const dataAttributeNames = Object.keys(attributes).filter((name) => name.startsWith("data-"));
      if (!attributes.id && !attributes.class && dataAttributeNames.length === 0) continue;
      const closeIndex = html.indexOf(`</${tagName}>`, match.index + match[0].length);
      const element = new FakeElement(tagName, attributes, this);
      element.textContent = textFromHtml(closeIndex >= 0 ? html.slice(match.index + match[0].length, closeIndex) : "");
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
  }

  updateElementAttribute(element, name, value) {
    this.unindexElement(element);
    element.attributes[name] = value;
    if (name === "id") element.id = value;
    if (name.startsWith("data-")) element.dataset = datasetFromAttributes(element.attributes);
    this.indexElement(element);
  }

  removeElementAttribute(element, name) {
    this.unindexElement(element);
    delete element.attributes[name];
    if (name === "id") element.id = "";
    if (name.startsWith("data-")) element.dataset = datasetFromAttributes(element.attributes);
    this.indexElement(element);
  }

  removeElement(element) {
    this.unindexElement(element);
  }
}

function parseAttributes(rawAttributes) {
  const attributes = {};
  for (const [, name, quotedValue, bareValue] of rawAttributes.matchAll(/([\w-]+)(?:="([^"]*)"|=(\S+))?/g)) {
    attributes[name] = unescapeHtml(quotedValue ?? bareValue ?? true);
  }
  return attributes;
}

function datasetFromAttributes(attributes) {
  const dataset = {};
  for (const [name, value] of Object.entries(attributes)) {
    if (!name.startsWith("data-")) continue;
    const key = name.slice(5).replaceAll(/-([a-z])/g, (_, letter) => letter.toUpperCase());
    dataset[key] = String(value);
  }
  return dataset;
}

function textFromHtml(html) {
  return unescapeHtml(String(html).replaceAll(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim());
}

function unescapeHtml(value) {
  return String(value)
    .replaceAll("&amp;", "&")
    .replaceAll("&lt;", "<")
    .replaceAll("&gt;", ">")
    .replaceAll("&quot;", '"')
    .replaceAll("&#039;", "'");
}

function payloadWithEvents(events) {
  return {
    meeting: {
      meeting_id: "resident-gui",
      question: "runtime feed",
      live_status: "running",
      roles: [],
      debate_rounds: [],
      moderator_synthesis: {},
      decision_gate: {},
    },
    live_events: events,
  };
}

function installHarness() {
  Object.assign(state, {
    payload: payloadWithEvents([]),
    sideChatEvents: [],
    liveAgentFlow: null,
    liveAgentFlowEvents: [],
  });
  globalThis.document = new FakeDocument();
  globalThis.localStorage = { getItem: () => "", setItem() {} };
  globalThis.requestAnimationFrame = (callback) => callback();
  globalThis.setTimeout = (callback) => {
    callback();
    return 0;
  };
  return { document: globalThis.document };
}

test("live transcript refresh preserves stable rows, appends new rows, updates changed rows, and keeps reader position", () => {
  const { document } = installHarness();
  const firstPayload = payloadWithEvents([
    { id: "live-a", kind: "message", display_name: "Codex", content: "첫 공식 발언", confidence: "medium", official_record: true },
    { id: "live-b", kind: "message", display_name: "Kiro", content: "두 번째 공식 발언", confidence: "medium", official_record: true },
  ]);
  state.payload = firstPayload;
  renderLive(firstPayload, { followLatest: false });
  const feed = document.querySelector(".live-transcript");
  feed.scrollHeight = 260;
  feed.clientHeight = 100;
  feed.scrollTop = 44;
  for (const listener of feed.listeners.get("scroll") || []) listener();
  const firstRow = document.querySelectorAll("[data-live-item-id]").find((row) => row.dataset.liveItemId === "live-a");
  const secondRow = document.querySelectorAll("[data-live-item-id]").find((row) => row.dataset.liveItemId === "live-b");

  const secondPayload = payloadWithEvents([
    { id: "live-a", kind: "message", display_name: "Codex", content: "첫 공식 발언", confidence: "medium", official_record: true },
    { id: "live-b", kind: "message", display_name: "Kiro", content: "두 번째 공식 발언 수정됨", confidence: "medium", official_record: true },
    { id: "live-c", kind: "message", display_name: "Grok", content: "세 번째 공식 발언", confidence: "medium", official_record: true },
  ]);
  refreshLiveTranscript(secondPayload, { followLatest: false });

  const rows = document.querySelectorAll("[data-live-item-id]");
  const updatedFirst = rows.find((row) => row.dataset.liveItemId === "live-a");
  const updatedSecond = rows.find((row) => row.dataset.liveItemId === "live-b");
  const appendedThird = rows.find((row) => row.dataset.liveItemId === "live-c");
  assert.equal(updatedFirst, firstRow);
  assert.equal(updatedSecond, secondRow);
  assert.ok(appendedThird);
  assert.match(updatedSecond.textContent, /수정됨/);
  assert.match(appendedThird.textContent, /세 번째 공식 발언/);
  assert.equal(feed.scrollTop, 44);
});
