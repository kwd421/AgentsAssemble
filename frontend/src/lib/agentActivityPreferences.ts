const STORAGE_KEY = "agentsassemble.agent-activity-visibility.v1";

export type AgentActivityVisibility = Record<string, boolean>;

export function loadAgentActivityVisibility(): AgentActivityVisibility {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(STORAGE_KEY) || "{}");
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return {};
    return Object.fromEntries(
      Object.entries(parsed).filter((entry): entry is [string, boolean] => typeof entry[1] === "boolean")
    );
  } catch {
    return {};
  }
}

export function persistAgentActivityVisibility(value: AgentActivityVisibility) {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(value));
  } catch {
    // A blocked storage API should not prevent the room from rendering.
  }
}

export function agentActivityIsVisible(value: AgentActivityVisibility, participantId: string) {
  return value[participantId] === true;
}
