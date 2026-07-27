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
}): string[] {
  const names = ["나"];
  const participantIds = new Set([clean(viewerParticipantId)].filter(Boolean));
  const labels = new Set(["나"]);

  function append(participantIdValue: unknown, displayNameValue: unknown) {
    const participantId = clean(participantIdValue);
    if (!participantId || participantIds.has(participantId)) return;
    participantIds.add(participantId);

    const displayName = clean(displayNameValue);
    const preferredLabel = displayName || participantId;
    const label = labels.has(preferredLabel.toLowerCase())
      ? participantId
      : preferredLabel;
    const key = label.toLowerCase();
    if (labels.has(key)) return;
    labels.add(key);
    names.push(label);
  }

  agents.forEach((agent) => append(agent.agent_id, agent.display_name));
  members.forEach((member) => append(member.participant_id, member.display_name));
  return names;
}
