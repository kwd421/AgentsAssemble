export type RoomPostingMode = "host" | "guest";
export type RoomPostingTransport = "host-lobby" | "room-say" | "blocked";

export type RoomPostingState = {
  mode: RoomPostingMode;
  canPost: boolean;
  transport: RoomPostingTransport;
  sessionToken: string;
  disabledReason: string;
};

export function roomPostingState({
  guestLocked,
  guestReadOnly,
  sessionToken,
}: {
  guestLocked: boolean;
  guestReadOnly: boolean;
  sessionToken?: string;
}): RoomPostingState {
  const cleanSessionToken = String(sessionToken || "").trim();
  if (!guestLocked) {
    return {
      mode: "host",
      canPost: true,
      transport: "host-lobby",
      sessionToken: "",
      disabledReason: "",
    };
  }
  if (guestReadOnly) {
    return {
      mode: "guest",
      canPost: false,
      transport: "blocked",
      sessionToken: cleanSessionToken,
      disabledReason: "읽기 전용 초대입니다. 이 방은 보기만 가능합니다.",
    };
  }
  if (!cleanSessionToken) {
    return {
      mode: "guest",
      canPost: false,
      transport: "blocked",
      sessionToken: "",
      disabledReason: "메시지를 보내려면 유효한 초대 세션이 필요합니다.",
    };
  }
  return {
    mode: "guest",
    canPost: true,
    transport: "room-say",
    sessionToken: cleanSessionToken,
    disabledReason: "",
  };
}
