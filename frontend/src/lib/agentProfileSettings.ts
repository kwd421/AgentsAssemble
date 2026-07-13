export type AgentProfileSettings = {
  displayName?: string;
  avatarImage?: string;
};

const AGENT_PROFILE_SETTINGS_KEY = "agentsassemble.agentProfiles.v1";

function cleanText(value: unknown, limit: number): string {
  return String(value || "")
    .replace(/[\r\n\t]/g, " ")
    .trim()
    .slice(0, limit)
    .trim();
}

function cleanAvatarImage(value: unknown): string {
  const text = cleanText(value, 4096);
  if (!text) return "";
  if (text.startsWith("/api/attachments/") || text.startsWith("data:image/")) return text;
  return "";
}

function normalizeSettings(value: unknown): AgentProfileSettings {
  if (!value || typeof value !== "object") return {};
  const record = value as Record<string, unknown>;
  return {
    displayName: cleanText(record.displayName, 80) || undefined,
    avatarImage: cleanAvatarImage(record.avatarImage) || undefined,
  };
}

export function loadAgentProfileSettings(): Record<string, AgentProfileSettings> {
  try {
    const raw = window.localStorage.getItem(AGENT_PROFILE_SETTINGS_KEY);
    const parsed = raw ? JSON.parse(raw) : {};
    if (!parsed || typeof parsed !== "object") return {};
    return Object.fromEntries(
      Object.entries(parsed as Record<string, unknown>)
        .map(([agentId, value]) => [cleanText(agentId, 128), normalizeSettings(value)] as const)
        .filter(([agentId]) => Boolean(agentId))
    );
  } catch {
    return {};
  }
}

export function saveAgentProfileSettings(
  agentId: string,
  settings: AgentProfileSettings
): Record<string, AgentProfileSettings> {
  const cleanAgentId = cleanText(agentId, 128);
  if (!cleanAgentId) return loadAgentProfileSettings();
  const previous = loadAgentProfileSettings();
  const normalized = normalizeSettings(settings);
  const next = {
    ...previous,
    [cleanAgentId]: normalized,
  };
  try {
    window.localStorage.setItem(AGENT_PROFILE_SETTINGS_KEY, JSON.stringify(next));
  } catch {
    // Agent profile settings are local UI preferences; keep returning the in-memory projection.
  }
  return next;
}

export function removeAgentProfileSettings(agentId: string): Record<string, AgentProfileSettings> {
  const cleanAgentId = cleanText(agentId, 128);
  const next = loadAgentProfileSettings();
  if (!cleanAgentId || !(cleanAgentId in next)) return next;
  delete next[cleanAgentId];
  try {
    window.localStorage.setItem(AGENT_PROFILE_SETTINGS_KEY, JSON.stringify(next));
  } catch {
    // Canonical room state remains authoritative even if legacy local cleanup fails.
  }
  return next;
}
