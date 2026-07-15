import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { joinRoomInvite, preflightRoomInvite, type RoomInviteJoinResponse } from "../api";
import { GUEST_SESSION_EXPIRED_MESSAGE } from "../lib/apiErrors";
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
  onRoomJoined: (room: RoomDockItem) => void;
  onResetToLobby: () => void;
};

export function useRoomAdmission({
  guestInvite,
  guestJoinToken,
  initialSession,
  onRoomJoined,
  onResetToLobby,
}: RoomAdmissionOptions) {
  const [guestSession, setGuestSession] = useState<RoomGuestSession | null>(initialSession);
  const [guestExpired, setGuestExpired] = useState(false);
  const [guestJoinRequested, setGuestJoinRequested] = useState(false);
  const [pendingGuestDisplayName, setPendingGuestDisplayName] = useState("Guest");
  const [pendingGuestAvatarImage, setPendingGuestAvatarImage] = useState("");
  const [guestJoinStatus, setGuestJoinStatus] = useState("");
  const [guestAdmissionResolved, setGuestAdmissionResolved] = useState(!guestJoinToken);
  const [guestAdmissionBusy, setGuestAdmissionBusy] = useState(Boolean(guestJoinToken));
  const preflightAttemptedTokenRef = useRef("");

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
    if (!guestAdmissionResolved) return;
    setGuestJoinStatus("");
    setGuestJoinRequested(true);
  }, [guestAdmissionResolved]);

  const clearInviteUrl = useCallback(() => {
    try {
      window.history.replaceState({}, "", window.location.pathname || "/join");
    } catch {
      // URL cleanup is best-effort; verified session state remains authoritative.
    }
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
      setGuestAdmissionBusy(false);
      setGuestAdmissionResolved(true);
      onRoomJoined(roomFromGuestSession(nextSession));
      setGuestJoinStatus("");
      clearInviteUrl();
    },
    [clearInviteUrl, onRoomJoined, pendingGuestDisplayName]
  );

  useEffect(() => {
    if (!guestJoinToken || guestExpired) return;
    if (preflightAttemptedTokenRef.current === guestJoinToken) return;
    preflightAttemptedTokenRef.current = guestJoinToken;
    let cancelled = false;
    setGuestAdmissionBusy(true);
    setGuestAdmissionResolved(false);
    setGuestJoinStatus("초대와 기존 신원을 확인하는 중...");
    preflightRoomInvite({
      inviteToken: guestJoinToken,
      deviceToken: getOrCreateDeviceToken(),
      sessionToken: guestSession?.sessionToken || "",
    })
      .then((decision) => {
        if (cancelled) return;
        if (decision.status === "existing_session" && guestSession) {
          const preservedSession = {
            ...guestSession,
            roomLabel: decision.room_label || guestSession.roomLabel,
            inviteScope: decision.invite_scope || guestSession.inviteScope,
          };
          setGuestSession(preservedSession);
          setGuestAdmissionResolved(true);
          setGuestAdmissionBusy(false);
          setGuestJoinStatus("");
          onRoomJoined(roomFromGuestSession(preservedSession));
          clearInviteUrl();
          return;
        }
        if (
          decision.can_auto_join &&
          (decision.status === "known_user" || decision.status === "existing_member") &&
          decision.participant
        ) {
          setPendingGuestDisplayName(decision.participant.display_name || "Guest");
          setPendingGuestAvatarImage(decision.participant.avatar_image_url || "");
          setGuestAdmissionResolved(true);
          setGuestAdmissionBusy(false);
          setGuestJoinStatus("");
          setGuestJoinRequested(true);
          return;
        }
        if (decision.status === "profile_required") {
          const remembered = loadRememberedGuestProfile();
          if (remembered) {
            setPendingGuestDisplayName(remembered.displayName);
            setPendingGuestAvatarImage(remembered.avatarImage || "");
          }
          setGuestAdmissionResolved(true);
          setGuestAdmissionBusy(false);
          setGuestJoinStatus("");
          return;
        }
        setGuestAdmissionResolved(false);
        setGuestAdmissionBusy(false);
        setGuestJoinStatus(
          decision.status === "invite_expired"
            ? "초대 링크가 만료되었습니다."
            : decision.reason || "유효하지 않은 초대 링크입니다."
        );
      })
      .catch((error) => {
        if (cancelled) return;
        setGuestAdmissionResolved(false);
        setGuestAdmissionBusy(false);
        setGuestJoinStatus(error instanceof Error ? error.message : "초대 확인 실패");
      });
    return () => {
      cancelled = true;
    };
  }, [clearInviteUrl, guestExpired, guestJoinToken, guestSession, onRoomJoined]);

  useEffect(() => {
    if (!guestJoinToken || guestAlreadyJoinedThisInvite) return;
    if (!guestAdmissionResolved || !guestJoinRequested) return;
    let cancelled = false;
    setGuestAdmissionBusy(true);
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
          setGuestAdmissionBusy(false);
          onRoomJoined(roomFromGuestSession(restoredSession));
          setGuestJoinStatus("");
          clearInviteUrl();
          return;
        }
        setGuestJoinStatus(error instanceof Error ? error.message : "초대 링크 입장 실패");
        setGuestAdmissionBusy(false);
        setGuestJoinRequested(false);
      });
    return () => {
      cancelled = true;
    };
  }, [
    applyJoinedSession,
    clearInviteUrl,
    guestAdmissionResolved,
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
    guestAdmissionBusy,
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
