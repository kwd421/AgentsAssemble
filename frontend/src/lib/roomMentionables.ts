import type { Mentionable } from "./mentionComposerModel";

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
    { token: string; displayName: string }
  >();

  function append(participantIdValue: unknown, displayNameValue: unknown) {
    const participantId = clean(participantIdValue);
    const key = participantId.toLowerCase();
    if (!participantId || participantId === viewerId || participants.has(key)) return;
    participants.set(key, {
      token: participantId,
      displayName: clean(displayNameValue),
    });
  }

  agents.forEach((agent) => append(agent.agent_id, agent.display_name));
  members.forEach((member) => append(member.participant_id, member.display_name));
  const viewerDisplayName = clean(
    members.find((member) => clean(member.participant_id) === viewerId)?.display_name
  );
  const displayNameCounts = new Map<string, number>();
  participants.forEach(({ displayName }) => {
    const key = displayName.toLowerCase();
    if (key) displayNameCounts.set(key, (displayNameCounts.get(key) || 0) + 1);
  });
  return [
    ...(viewerId && viewerDisplayName
      ? [{ token: viewerId, label: viewerDisplayName }]
      : []),
    ...Array.from(participants.values(), ({ token, displayName }) => {
      const uniqueDisplayName =
        displayName && displayNameCounts.get(displayName.toLowerCase()) === 1;
      return {
        token,
        label: uniqueDisplayName
          ? displayName
          : displayName
            ? `${displayName} · ${token}`
            : token,
      };
    }),
  ];
}
