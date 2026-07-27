import type { Mentionable } from "./mentionComposerModel";

const DISPLAY_ALIAS_SPLIT = /[\s/|·—–:()]+/u;
const SAFE_MENTION_ALIAS = /^[\p{L}\p{N}_.-]+$/u;

type AgentMentionIdentity = {
  agent_id: string;
  display_name?: string;
};

type MemberMentionIdentity = {
  participant_id: string;
  display_name?: string;
};

function clean(value: unknown) {
  return String(value || "").trim();
}

export function roomMentionables({
  viewerParticipantId,
  agents,
  members,
}: {
  viewerParticipantId: string;
  agents: AgentMentionIdentity[];
  members: MemberMentionIdentity[];
}): Mentionable[] {
  const viewerId = clean(viewerParticipantId);
  const participants = new Map<
    string,
    { token: string; displayName: string; agent: boolean }
  >();

  function append(
    participantIdValue: unknown,
    displayNameValue: unknown,
    agent: boolean
  ) {
    const participantId = clean(participantIdValue);
    const key = participantId.toLowerCase();
    if (!participantId || participantId === viewerId || participants.has(key)) return;
    participants.set(key, {
      token: participantId,
      displayName: clean(displayNameValue),
      agent,
    });
  }

  agents.forEach((agent) => append(agent.agent_id, agent.display_name, true));
  members.forEach((member) => append(member.participant_id, member.display_name, false));
  const displayNameCounts = new Map<string, number>();
  participants.forEach(({ displayName }) => {
    const key = displayName.toLowerCase();
    if (key) displayNameCounts.set(key, (displayNameCounts.get(key) || 0) + 1);
  });
  const routingAliasCounts = new Map<string, number>();
  const routingAliases = new Map<string, string[]>();
  participants.forEach(({ token, displayName, agent }) => {
    if (!agent) return;
    const aliases = [
      token,
      ...displayName.split(DISPLAY_ALIAS_SPLIT),
    ].filter((alias) => alias && alias.toLowerCase() !== "all" && SAFE_MENTION_ALIAS.test(alias));
    routingAliases.set(token.toLowerCase(), aliases);
    new Set(aliases.map((alias) => alias.toLowerCase())).forEach((alias) => {
      routingAliasCounts.set(alias, (routingAliasCounts.get(alias) || 0) + 1);
    });
  });
  return [
    { token: "나", label: "나" },
    ...Array.from(participants.values(), ({ token, displayName, agent }) => {
      const preferredAlias = agent
        ? (routingAliases.get(token.toLowerCase()) || []).find(
            (alias) =>
              alias.toLowerCase() !== token.toLowerCase() &&
              routingAliasCounts.get(alias.toLowerCase()) === 1
          )
        : "";
      return {
        token: preferredAlias || token,
        label:
          displayName && displayNameCounts.get(displayName.toLowerCase()) === 1
            ? displayName
            : displayName
              ? `${displayName} · ${token}`
              : token,
      };
    }),
  ];
}
