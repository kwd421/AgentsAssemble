export function agentSessionResumeStatus(response: {
  state_status?: string;
  process_status?: string;
  status?: string;
}) {
  if (response.process_status === "resumed" || response.process_status === "launched") {
    return "Agent Session process resumed";
  }
  if (response.process_status === "unsupported") return "Agent Session state attached · process unsupported";
  if (response.process_status === "failed") return "Agent Session state attached · process failed";
  if (response.process_status === "not_started") return "Agent Session state attached only";
  return `Agent Session ${response.state_status || response.status || "attached"}`;
}
