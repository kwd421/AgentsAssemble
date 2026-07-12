import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { joinRoomInvite, type RoomInviteJoinResponse } from "../api";
import { GUEST_SESSION_EXPIRED_MESSAGE, isUnauthorizedApiError } from "../lib/apiErrors";
import { getOrCreateDeviceToken, loadRememberedGuestProfile, rememberGuestProfile } from "../lib/deviceIdentity";
import { roomFromGuestSession, type RoomDockItem } from "../lib/roomDockModel";
import {
  loadRoomGuestSession,
  persistRoomGuestSession,
  roomGuestSessionExpired,
  roomGuestSessionFromJoinPayload,
  type RoomGuestSession,
} from "../lib/roomGuestSession";

type RoomAdmissionOptions = {
  guestInvite: RoomDockItem | null;
  guestJoinToken: string;
  initialSession: RoomGuestSession | null;
  flowError: Error | null;
  onRoomJoined: (room: RoomDockItem) => void;
  onResetToLobby: () => void;
};

export function useRoomAdmission({
  guestInvite,
  guestJoinToken,
  initialSession,
  flowError,
  onRoomJoined,
  onResetToLobby,
}: RoomAdmissionOptions) {
  const [guestSession, setGuestSession] = useState<RoomGuestSession | null>(initialSession);
  const [guestExpired, setGuestExpired] = useState(false);
  const [guestJoinRequested, setGuestJoinRequested] = useState(false);
  const [pendingGuestDisplayName, setPendingGuestDisplayName] = useState("Guest");
  const [pendingGuestAvatarImage, setPendingGuestAvatarImage] = useState("");
  const [guestJoinStatus, setGuestJoinStatus] = useState("");
  const autoJoinAttemptedTokenRef = useRef("");

  const guestLocked = Boolean(guestInvite || guestSession || guestJoinToken || guestExpired);
  const guestMeetingId = guestSession?.meetingId || guestInvite?.meetingId || "";
  const guestJoinPending = Boolean(guestJoinToken && guestSession?.inviteToken !== guestJoinToken);
  const guestReadOnly =
    guestInvite?.inviteScope === "read_only" || guestSession?.inviteScope === "read_only";
  const guestAlreadyJoinedThisInvite = Boolean(
    guestJoinToken &&
      guestSession?.inviteToken === guestJoinToken &&
      !roomGuestSessionExpired(guestSession)
  );

  const guestPanelProfile = useMemo(
    () =>
      guestLocked
        ? {
            displayName:
              guestSession?.displayName ||
              (guestJoinPending ? "입장 확인 중" : guestExpired ? "게스트 세션 만료" : "게스트"),
            avatarLabel:
              (guestSession?.displayName || guestSession?.agentId || "G").slice(0, 1).toUpperCase() || "G",
            avatarImage: guestSession?.avatarImage,
            statusLabel: guestExpired
              ? "세션 만료"
              : guestJoinPending
              ? "초대 확인 중"
              : guestSession?.sessionToken
              ? "게스트로 접속"
              : "읽기 전용 미리보기",
            expired: guestExpired,
          }
        : undefined,
    [guestExpired, guestJoinPending, guestLocked, guestSession]
  );

  const expireGuestSession = useCallback(() => {
    persistRoomGuestSession(null);
    setGuestSession(null);
    setGuestExpired(true);
    setGuestJoinStatus(GUEST_SESSION_EXPIRED_MESSAGE);
    onResetToLobby();
  }, [onResetToLobby]);

  const clearGuestSession = useCallback(() => {
    persistRoomGuestSession(null);
    setGuestSession(null);
  }, []);

  const requestGuestJoin = useCallback(() => {
    setGuestJoinStatus("");
    setGuestJoinRequested(true);
  }, []);

  const applyJoinedSession = useCallback(
    (inviteToken: string, payload: RoomInviteJoinResponse, avatarImage: string) => {
      const nextSession = roomGuestSessionFromJoinPayload(inviteToken, {
        ...payload,
        avatar_image_url: payload.avatar_image_url || avatarImage,
      });
      persistRoomGuestSession(nextSession);
      rememberGuestProfile({
        displayName: nextSession.displayName || pendingGuestDisplayName,
        avatarImage: nextSession.avatarImage || avatarImage || undefined,
      });
      setGuestSession(nextSession);
      setGuestExpired(false);
      setGuestJoinRequested(false);
      onRoomJoined(roomFromGuestSession(nextSession));
      setGuestJoinStatus("");
      try {
        window.history.replaceState({}, "", window.location.pathname || "/join");
      } catch {
        // URL cleanup is best-effort; the session is already stored in memory.
      }
    },
    [onRoomJoined, pendingGuestDisplayName]
  );

  useEffect(() => {
    if (guestLocked && guestSession?.sessionToken && isUnauthorizedApiError(flowError)) {
      expireGuestSession();
    }
  }, [expireGuestSession, flowError, guestLocked, guestSession?.sessionToken]);

  useEffect(() => {
    if (!guestJoinToken || guestAlreadyJoinedThisInvite) return;
    if (guestJoinRequested || guestExpired) return;
    const remembered = loadRememberedGuestProfile();
    if (!remembered) return;
    if (autoJoinAttemptedTokenRef.current === guestJoinToken) return;
    autoJoinAttemptedTokenRef.current = guestJoinToken;
    setPendingGuestDisplayName(remembered.displayName);
    setPendingGuestAvatarImage(remembered.avatarImage || "");
    setGuestJoinRequested(true);
  }, [guestAlreadyJoinedThisInvite, guestExpired, guestJoinRequested, guestJoinToken]);

  useEffect(() => {
    if (!guestJoinToken || guestAlreadyJoinedThisInvite) return;
    if (!guestJoinRequested) return;
    let cancelled = false;
    setGuestJoinStatus("초대 링크로 방에 입장 중...");
    joinRoomInvite({
      inviteToken: guestJoinToken,
      displayName: pendingGuestDisplayName,
      avatarImage: pendingGuestAvatarImage,
      deviceToken: getOrCreateDeviceToken(),
      participantType: "human",
    })
      .then((payload) => {
        if (!cancelled) applyJoinedSession(guestJoinToken, payload, pendingGuestAvatarImage);
      })
      .catch((error) => {
        if (cancelled) return;
        const restoredSession = loadRoomGuestSession();
        if (restoredSession?.inviteToken === guestJoinToken) {
          setGuestSession(restoredSession);
          setGuestExpired(false);
          onRoomJoined(roomFromGuestSession(restoredSession));
          setGuestJoinStatus("");
          try {
            window.history.replaceState({}, "", window.location.pathname || "/join");
          } catch {
            // URL cleanup is best-effort; the restored session remains in memory.
          }
          return;
        }
        setGuestJoinStatus(error instanceof Error ? error.message : "초대 링크 입장 실패");
        setGuestJoinRequested(false);
      });
    return () => {
      cancelled = true;
    };
  }, [
    applyJoinedSession,
    guestAlreadyJoinedThisInvite,
    guestJoinRequested,
    guestJoinToken,
    onRoomJoined,
    pendingGuestAvatarImage,
    pendingGuestDisplayName,
  ]);

  return {
    guestSession,
    guestExpired,
    guestJoinRequested,
    pendingGuestDisplayName,
    pendingGuestAvatarImage,
    guestJoinStatus,
    guestLocked,
    guestMeetingId,
    guestJoinPending,
    guestReadOnly,
    guestPanelProfile,
    setPendingGuestDisplayName,
    setPendingGuestAvatarImage,
    requestGuestJoin,
    expireGuestSession,
    clearGuestSession,
  };
}
