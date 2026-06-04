import test from "node:test";
import assert from "node:assert/strict";
import { canViewAgentQuota } from "../src/lib/agentQuotaVisibility.ts";

test("viewer can see quota for explicitly owned remote companion AI", () => {
  assert.equal(
    canViewAgentQuota(
      { agent_id: "guest-abc-ai", connection_kind: "native_remote_room_client" },
      { ownedAgentIds: ["guest-abc-ai"] }
    ),
    true
  );
});

test("viewer cannot see quota for another person's remote AI", () => {
  assert.equal(
    canViewAgentQuota(
      { agent_id: "friend-claude", connection_kind: "native_remote_room_client" },
      { ownedAgentIds: ["guest-abc-ai"] }
    ),
    false
  );
});

test("host can see quota for local process agents", () => {
  assert.equal(
    canViewAgentQuota(
      { agent_id: "codex-host", connection_kind: "live_session" },
      {
        hostCanViewLocalAgentQuotas: true,
        localProcessAgentIds: ["codex-host"],
      }
    ),
    true
  );
});

test("host still hides quota for remote-owner agents", () => {
  assert.equal(
    canViewAgentQuota(
      { agent_id: "friend-bridge", connection_kind: "remote_bridge" },
      {
        hostCanViewLocalAgentQuotas: true,
        localProcessAgentIds: ["friend-bridge"],
      }
    ),
    false
  );
});
