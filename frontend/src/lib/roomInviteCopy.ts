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

export function remoteClientPacketPreview(packet: unknown): string {
  if (!packet) return "";
  return JSON.stringify(packet, null, 2);
}

const LOCAL_INVITE_HOSTS = new Set(["localhost", "127.0.0.1", "0.0.0.0", "::1"]);

export function isExternalInviteUrl(url: string): boolean {
  const value = String(url || "").trim();
  if (!value) return false;
  try {
    const parsed = new URL(value);
    const hostname = parsed.hostname.toLowerCase();
    return (
      (parsed.protocol === "http:" || parsed.protocol === "https:") &&
      Boolean(hostname) &&
      !LOCAL_INVITE_HOSTS.has(hostname) &&
      parsed.pathname === "/join" &&
      Boolean(parsed.searchParams.get("token"))
    );
  } catch {
    return false;
  }
}

export function secureInviteCopyTarget({
  joinUrl,
  localPreviewUrl,
}: {
  joinUrl?: string;
  localPreviewUrl?: string;
}): {
  copyUrl: string;
  status: string;
  previewLabel: string;
  secure: boolean;
} {
  const cleanJoinUrl = String(joinUrl || "").trim();
  if (isExternalInviteUrl(cleanJoinUrl)) {
    return {
      copyUrl: cleanJoinUrl,
      status: "보안 초대 링크 복사됨",
      previewLabel: "로컬/dev 미리보기 링크",
      secure: true,
    };
  }
  const hasJoinUrl = Boolean(cleanJoinUrl);
  const hasLocalPreview = Boolean(String(localPreviewUrl || "").trim());
  return {
    copyUrl: "",
    status: hasJoinUrl
      ? "외부 초대 링크가 아직 준비되지 않았습니다. 공개 URL 또는 터널을 먼저 설정하세요."
      : hasLocalPreview
      ? "공개 URL이 없어 보안 초대 링크를 만들 수 없습니다. 아래 링크는 로컬/dev 미리보기 전용입니다."
      : "공개 URL이 없어 보안 초대 링크를 만들 수 없습니다.",
    previewLabel: "로컬/dev 미리보기 링크",
    secure: false,
  };
}
