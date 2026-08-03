import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { LiveAgent } from "../../../api";
import MemberUsage from "./MemberUsage";
import type { MemberEntry } from "./memberTypes";

afterEach(cleanup);

describe("MemberUsage", () => {
  it("labels retained quota values as stale after refresh fails", () => {
    const agent = {
      agent_id: "agent-1",
      display_name: "Agent One",
      status: "online",
      provider_kind: "codex_live_session",
      connection_kind: "agent_session",
      engagement_mode: "agent_session",
      meeting_id: "room-1",
      last_seen_at: "",
      last_reply_at: "",
      sandbox_enforcement: "read-only",
      capabilities: [],
      quota_status: "stale",
      quota_5h: "23%",
    } satisfies LiveAgent;
    const entry = {
      displayName: "Agent One",
      canViewQuota: true,
    } as MemberEntry;

    render(<MemberUsage entry={entry} agent={agent} />);

    expect(screen.getByText(/마지막으로 확인된 값/)).toBeTruthy();
    expect(screen.getByText("23%")).toBeTruthy();
  });

  it("renders a USD account balance without a redundant currency suffix", () => {
    const agent = {
      agent_id: "agent-1",
      display_name: "Agent One",
      status: "online",
      provider_kind: "deepseek_api",
      connection_kind: "agent_session",
      engagement_mode: "agent_session",
      meeting_id: "room-1",
      last_seen_at: "",
      last_reply_at: "",
      sandbox_enforcement: "read-only",
      capabilities: [],
      account_available: true,
      account_balances: [{ currency: "USD", amount: "12.34" }],
    } satisfies LiveAgent;
    const entry = {
      displayName: "Agent One",
      canViewQuota: true,
    } as MemberEntry;

    render(<MemberUsage entry={entry} agent={agent} />);

    expect(screen.getByText("$12.34")).toBeTruthy();
    expect(screen.queryByText("12.34 USD")).toBeNull();
  });
});
