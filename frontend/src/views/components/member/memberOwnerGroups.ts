import type { MemberEntry } from "./memberTypes";

export type MemberOwnerGroup = {
  id: string;
  label: string;
  person?: MemberEntry;
  agents: MemberEntry[];
};

function isAgentEntry(entry: MemberEntry) {
  return Boolean(entry.agent || entry.agentSession) || entry.role !== "human";
}

function matchesQuery(entry: MemberEntry, needle: string) {
  if (!needle) return true;
  return [
    entry.displayName,
    entry.agentDisplayName,
    entry.ownerDisplayName,
    entry.detail,
    entry.role,
  ].some((value) => String(value || "").toLowerCase().includes(needle));
}

export function buildMemberOwnerGroups(
  entries: MemberEntry[],
  viewerParticipantId: string,
  query: string
): MemberOwnerGroup[] {
  const people = entries.filter((entry) => !isAgentEntry(entry));
  const groups: MemberOwnerGroup[] = people.map((person) => ({
    id: person.id,
    label: person.displayName,
    person,
    agents: [] as MemberEntry[],
  }));
  const groupByOwnerId = new Map(groups.map((group) => [group.id, group]));

  entries.filter(isAgentEntry).forEach((agent) => {
    const requestedOwnerId = String(agent.ownerId || "").trim();
    const ownerId =
      agent.ownedByViewer && !groupByOwnerId.has(requestedOwnerId)
        ? viewerParticipantId
        : requestedOwnerId;
    let group: MemberOwnerGroup | undefined = groupByOwnerId.get(ownerId);
    if (!group) {
      const groupId = ownerId || `unassigned:${agent.ownerDisplayName || "agents"}`;
      group = groupByOwnerId.get(groupId);
      if (!group) {
        group = {
          id: groupId,
          label: agent.ownerDisplayName || "소유자 정보 없음",
          agents: [],
        };
        groups.push(group);
        groupByOwnerId.set(groupId, group);
      }
    }
    if (!group) return;
    group.agents.push(agent);
  });

  const needle = query.trim().toLowerCase();
  if (!needle) return groups;
  return groups.flatMap((group) => {
    const ownerMatches =
      String(group.label || "").toLowerCase().includes(needle) ||
      Boolean(group.person && matchesQuery(group.person, needle));
    const matchingAgents = group.agents.filter((agent) => matchesQuery(agent, needle));
    if (!ownerMatches && matchingAgents.length === 0) return [];
    return [{ ...group, agents: ownerMatches ? group.agents : matchingAgents }];
  });
}
