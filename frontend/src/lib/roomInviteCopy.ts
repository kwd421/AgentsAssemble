export function inviteFriendButtonLabel({
  status,
  isAiFriend,
  readOnlyInvite,
}: {
  status?: string;
  isAiFriend: boolean;
  readOnlyInvite: boolean;
}): string {
  if (status) return status;
  if (readOnlyInvite) return isAiFriend ? "읽기 전용 호출" : "읽기 전용 초대";
  return isAiFriend ? "호출하기" : "초대하기";
}

export function inviteFriendDmMessage({
  roomLabel,
  link,
  isAiFriend,
  isLiveSession,
  readOnlyInvite,
}: {
  roomLabel: string;
  link: string;
  isAiFriend: boolean;
  isLiveSession: boolean;
  readOnlyInvite: boolean;
}): string {
  if (!isAiFriend) {
    return readOnlyInvite ? `${roomLabel} 읽기 전용 초대: ${link}` : `${roomLabel} 초대: ${link}`;
  }
  if (isLiveSession) {
    return readOnlyInvite ? `${roomLabel} 읽기 전용 호출: ${link}` : `${roomLabel} 호출: ${link}`;
  }
  return readOnlyInvite
    ? `${roomLabel} 읽기 전용 초대 링크가 생성됐지만 이 AI 세션은 현재 실행 중이 아닙니다. provider 세션을 시작하거나 resume해야 참가할 수 있습니다: ${link}`
    : `${roomLabel} 초대 링크가 생성됐지만 이 AI 세션은 현재 실행 중이 아닙니다. provider 세션을 시작하거나 resume해야 참가할 수 있습니다: ${link}`;
}

export function remoteClientPacketPreview(packet: unknown): string {
  if (!packet) return "";
  return JSON.stringify(packet, null, 2);
}
