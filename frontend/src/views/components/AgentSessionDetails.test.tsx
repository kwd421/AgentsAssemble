import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { RoomAgentSession } from "../../api";
import AgentSessionDetails from "./AgentSessionDetails";

afterEach(cleanup);

describe("AgentSessionDetails diagnostics", () => {
  it("shows which provider permissions were denied", () => {
    const session: RoomAgentSession = {
      room_id: "room-1",
      session_id: "session-1",
      participant_id: "agent-1",
      display_name: "Agent One",
      status: "error",
      runtime_status: "error",
      enabled: true,
      provider_kind: "grok_acp",
      runtime_kind: "acp",
      connection_kind: "agent_session",
      permission_request_count: 2,
      permission_denied_count: 2,
      denied_permission_names: ["shell.execute", "files.write"],
    };

    render(<AgentSessionDetails session={session} />);
    fireEvent.click(screen.getByText("고급 진단"));

    expect(screen.getByText(/shell\.execute/)).toBeTruthy();
    expect(screen.getByText(/files\.write/)).toBeTruthy();
  });
});
