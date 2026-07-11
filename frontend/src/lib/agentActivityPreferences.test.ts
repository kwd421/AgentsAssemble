import { beforeEach, describe, expect, it } from "vitest";

import {
  agentActivityIsVisible,
  loadAgentActivityVisibility,
  persistAgentActivityVisibility,
} from "./agentActivityPreferences";

describe("agent activity visibility", () => {
  beforeEach(() => {
    const values = new Map<string, string>();
    Object.defineProperty(window, "localStorage", {
      configurable: true,
      value: {
        getItem: (key: string) => values.get(key) ?? null,
        setItem: (key: string, value: string) => values.set(key, value),
      },
    });
  });

  it("defaults to visible and persists an explicit hidden preference", () => {
    expect(agentActivityIsVisible({}, "codex")).toBe(true);

    persistAgentActivityVisibility({ codex: false });
    const loaded = loadAgentActivityVisibility();

    expect(loaded).toEqual({ codex: false });
    expect(agentActivityIsVisible(loaded, "codex")).toBe(false);
  });
});
