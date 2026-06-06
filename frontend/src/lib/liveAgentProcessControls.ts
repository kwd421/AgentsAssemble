import type { LiveAgentProcessGroup } from "../api";

export type AgentProcessIdentity = {
  agent_id?: string;
  display_name?: string;
};

export type RegisteredAgentProcess = AgentProcessIdentity & {
  meeting_id?: string;
  provider_kind?: string;
  connection_kind?: string;
  status?: string;
  process_group_id?: string;
  live_agent_config_path?: string;
};

function cleanIdentityValue(value: unknown): string {
  return String(value || "").trim();
}

function sameKnownValue(left: unknown, right: unknown): boolean {
  const cleanLeft = cleanIdentityValue(left);
  const cleanRight = cleanIdentityValue(right);
  return Boolean(cleanLeft && cleanRight && cleanLeft === cleanRight);
}

function processGroupAgents(group?: LiveAgentProcessGroup) {
  return Array.isArray(group?.agents) ? group.agents : [];
}

export function processGroupAgentCount(group?: LiveAgentProcessGroup): number {
  return processGroupAgents(group).length;
}

export function processGroupOwnsAgent(
  group: LiveAgentProcessGroup | undefined,
  agent: AgentProcessIdentity
): boolean {
  const agentId = cleanIdentityValue(agent.agent_id);
  if (!agentId) return false;
  const groupedAgents = processGroupAgents(group);
  if (groupedAgents.length === 0) return false;
  return groupedAgents.some((candidate) => sameKnownValue(candidate.agent_id, agentId));
}

export function findProcessGroupForAgent(
  groups: LiveAgentProcessGroup[],
  agent: AgentProcessIdentity
): LiveAgentProcessGroup | undefined {
  const matchingGroups = groups.filter((group) => processGroupOwnsAgent(group, agent));
  const runningGroups = matchingGroups.filter((group) => group.status === "running");
  return (
    runningGroups.find((group) => processGroupAgentCount(group) === 1) ||
    runningGroups[0] ||
    matchingGroups.find((group) => processGroupAgentCount(group) === 1) ||
    matchingGroups[0]
  );
}

export function registeredAgentProcessGroupForAgent(
  agent: RegisteredAgentProcess
): LiveAgentProcessGroup | undefined {
  const agentId = cleanIdentityValue(agent.agent_id);
  const meetingId = cleanIdentityValue(agent.meeting_id);
  const groupId = cleanIdentityValue(agent.process_group_id);
  const configPath = cleanIdentityValue(agent.live_agent_config_path);
  const status = cleanIdentityValue(agent.status);
  if (!agentId || !meetingId || !groupId || !configPath) return undefined;
  if (status === "online" || status === "working" || status === "running") return undefined;
  return {
    group_id: groupId,
    status: "stopped",
    meeting_id: meetingId,
    config_path: configPath,
    agents: [
      {
        agent_id: agentId,
        display_name: cleanIdentityValue(agent.display_name) || agentId,
        provider_kind: cleanIdentityValue(agent.provider_kind),
        connection_kind: cleanIdentityValue(agent.connection_kind),
      },
    ],
  };
}

export function processGroupCanControlSingleAgent(
  group: LiveAgentProcessGroup | undefined,
  agent: AgentProcessIdentity
): boolean {
  return processGroupAgentCount(group) === 1 && processGroupOwnsAgent(group, agent);
}

export function processGroupIndividualControlReason(
  group: LiveAgentProcessGroup | undefined,
  agent: AgentProcessIdentity,
  label = "이 AI"
): string {
  if (!group || !processGroupOwnsAgent(group, agent)) return "";
  const count = processGroupAgentCount(group);
  if (count > 1) {
    return `이 세션은 ${count}개 AI가 하나의 프로세스로 실행 중이라 ${label}만 RESUME/STOP(KILL)할 수 없습니다.`;
  }
  return "";
}
