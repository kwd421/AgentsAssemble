import { describe, expect, it } from "vitest";
import { providerUsageAfterFailure } from "./providerUsageState";

describe("providerUsageAfterFailure", () => {
  it("retains a previously verified value but marks it stale", () => {
    const previous = {
      provider_id: "codex",
      status: "ready" as const,
      source: "app_server",
      observed_at: "2026-08-01T05:00:00Z",
      quota_5h: "23%",
      quota_windows: [],
    };

    expect(providerUsageAfterFailure(previous, "codex")).toEqual({
      ...previous,
      status: "stale",
      error_code: "usage_unavailable",
    });
  });
});
