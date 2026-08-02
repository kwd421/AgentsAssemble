import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import {
  joinRoomInvite,
  preflightRoomInvite,
  redeemOperatorPairing,
  type RoomInviteJoinResponse,
} from "../api";
import { ApiError, GUEST_SESSION_EXPIRED_MESSAGE } from "../lib/apiErrors";
import {
  getOrCreateClientId,
  getOrCreateDeviceToken,
  loadRememberedGuestProfile,
  rememberGuestProfile,
} from "../lib/deviceIdentity";
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
  operatorPairingToken: string;
  onPairingTokenConsumed: () => void;
  initialSession: RoomGuestSession | null;
  onRoomJoined: (room: RoomDockItem) => void;
  onResetToLobby: () => void;
};

export type OperatorPairingState =
  | "idle"
  | "pairing"
  | "pairing_failed_retryable"
  | "pairing_failed_terminal"
  | "paired";

type AdmissionSource =
  | "initial"
  | "invite"
  | "pairing"
  | "existing_session"
  | "restored"
  | "recovery";
type AdmissionOperation = "preflight" | "join" | "pairing";

export type AdmissionState =
  | { kind: "idle"; session: null; status: "" }
  | { kind: "preflighting"; session: RoomGuestSession | null; status: string }
  | { kind: "profile_required"; session: RoomGuestSession | null; status: "" }
  | { kind: "joining"; session: RoomGuestSession | null; status: string }
  | {
      kind: "joined";
      session: RoomGuestSession;
      source: AdmissionSource;
      status: "";
    }
  | { kind: "pairing"; session: RoomGuestSession | null; status: string }
  | {
      kind: "failed";
      session: RoomGuestSession | null;
      operation: AdmissionOperation;
      code: string;
      message: string;
      retryable: boolean;
      status: string;
    }
  | { kind: "expired"; session: null; status: string };

type AdmissionAction =
  | { type: "preflight_started"; status: string }
  | { type: "profile_required" }
  | { type: "join_requested"; status: string }
  | { type: "pairing_started"; status: string }
  | { type: "joined"; session: RoomGuestSession; source: AdmissionSource }
  | {
      type: "failed";
      operation: AdmissionOperation;
      code: string;
      message: string;
      retryable: boolean;
      status: string;
    }
  | { type: "expired"; status: string }
  | { type: "session_cleared" };

function initialAdmissionState({
  guestJoinToken,
  operatorPairingToken,
  initialSession,
}: Pick<RoomAdmissionOptions, "guestJoinToken" | "operatorPairingToken" | "initialSession">): AdmissionState {
  if (operatorPairingToken) {
    return { kind: "pairing", session: initialSession, status: "" };
  }
  if (guestJoinToken) {
    return { kind: "preflighting", session: initialSession, status: "" };
  }
  if (initialSession) {
    if (roomGuestSessionExpired(initialSession)) {
      return { kind: "expired", session: null, status: GUEST_SESSION_EXPIRED_MESSAGE };
    }
    return { kind: "joined", session: initialSession, source: "initial", status: "" };
  }
  return { kind: "idle", session: null, status: "" };
}

function admissionReducer(state: AdmissionState, action: AdmissionAction): AdmissionState {
  switch (action.type) {
    case "preflight_started":
      return { kind: "preflighting", session: state.session, status: action.status };
    case "profile_required":
      return { kind: "profile_required", session: state.session, status: "" };
    case "join_requested":
      if (
        state.kind !== "preflighting" &&
        state.kind !== "profile_required" &&
        state.kind !== "joining" &&
        !(state.kind === "failed" && state.operation === "join")
      ) {
        return state;
      }
      return { kind: "joining", session: state.session, status: action.status };
    case "pairing_started":
      if (
        state.kind !== "pairing" &&
        !(state.kind === "failed" && state.operation === "pairing" && state.retryable)
      ) {
        return state;
      }
      return { kind: "pairing", session: state.session, status: action.status };
    case "joined":
      return { kind: "joined", session: action.session, source: action.source, status: "" };
    case "failed":
      return {
        kind: "failed",
        session: state.session,
        operation: action.operation,
        code: action.code,
        message: action.message,
        retryable: action.retryable,
        status: action.status,
      };
    case "expired":
      return { kind: "expired", session: null, status: action.status };
    case "session_cleared":
      if (state.kind === "idle" || state.kind === "expired") return state;
      if (state.kind === "joined") return { kind: "idle", session: null, status: "" };
      return { ...state, session: null };
  }
}

const ADMISSION_REQUEST_ID_STORAGE_KEY = "agentsassemble.roomAdmissionRequestId.v1";

function loadOrCreateAdmissionRequestId(): string {
  const existing = String(window.sessionStorage.getItem(ADMISSION_REQUEST_ID_STORAGE_KEY) || "").trim();
  if (existing) return existing;
  if (typeof crypto === "undefined" || typeof crypto.randomUUID !== "function") {
    throw new Error("이 브라우저에서는 안전한 입장 요청 ID를 만들 수 없습니다.");
  }
  const requestId = crypto.randomUUID();
  window.sessionStorage.setItem(ADMISSION_REQUEST_ID_STORAGE_KEY, requestId);
  return requestId;
}

function clearAdmissionRequestId(): void {
  try {
    window.sessionStorage.removeItem(ADMISSION_REQUEST_ID_STORAGE_KEY);
  } catch {
    // The completed room session is authoritative even if storage cleanup fails.
  }
}

function pairingFailureIsRetryable(error: unknown): boolean {
  if (!(error instanceof ApiError)) return true;
  return error.status === 408 || error.status === 429 || error.status >= 500;
}

export function useRoomAdmission({
  guestInvite,
  guestJoinToken,
  operatorPairingToken,
  onPairingTokenConsumed,
  initialSession,
  onRoomJoined,
  onResetToLobby,
}: RoomAdmissionOptions) {
  const [admissionState, dispatchAdmission] = useReducer(
    admissionReducer,
    { guestJoinToken, operatorPairingToken, initialSession },
    initialAdmissionState
  );
  const [pendingGuestDisplayName, setPendingGuestDisplayName] = useState("Guest");
  const [pendingGuestAvatarImage, setPendingGuestAvatarImage] = useState("");
  const [operatorPairingAttempt, setOperatorPairingAttempt] = useState(0);
  const preflightAttemptedTokenRef = useRef("");
  const pairingAttemptedTokenRef = useRef("");
  const onPairingTokenConsumedRef = useRef(onPairingTokenConsumed);
  useEffect(() => {
    onPairingTokenConsumedRef.current = onPairingTokenConsumed;
  }, [onPairingTokenConsumed]);

  useEffect(() => {
    if (admissionState.kind === "expired") {
      persistRoomGuestSession(null);
    }
  }, [admissionState.kind]);

  const guestSession = admissionState.session;
  const admittedSessionToken =
    admissionState.kind === "joined" && !roomGuestSessionExpired(admissionState.session)
      ? admissionState.session.sessionToken
      : "";
  const guestExpired = admissionState.kind === "expired";
  const guestJoinRequested = admissionState.kind === "joining";
  const guestAdmissionBusy =
    admissionState.kind === "preflighting" ||
    admissionState.kind === "joining" ||
    admissionState.kind === "pairing";
  const guestJoinStatus = admissionState.status;
  const operatorPairingState: OperatorPairingState =
    admissionState.kind === "pairing"
      ? "pairing"
      : admissionState.kind === "failed" && admissionState.operation === "pairing"
      ? admissionState.retryable
        ? "pairing_failed_retryable"
        : "pairing_failed_terminal"
      : admissionState.kind === "joined" && admissionState.source === "pairing"
      ? "paired"
      : "idle";

  const guestLocked = Boolean(
    guestInvite ||
      guestSession ||
      guestJoinToken ||
      (operatorPairingState !== "idle" && operatorPairingState !== "paired") ||
      guestExpired
  );
  const guestMeetingId = guestSession?.meetingId || guestInvite?.meetingId || "";
  const guestJoinPending = Boolean(guestJoinToken && guestSession?.inviteToken !== guestJoinToken);
  const operatorPairingPending = Boolean(
    operatorPairingState !== "idle" &&
      operatorPairingState !== "paired" &&
      !guestSession?.operator
  );
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
              : guestSession?.operator
              ? "운영자로 접속"
              : operatorPairingState === "pairing_failed_retryable"
              ? "운영자 연결 재시도 가능"
              : operatorPairingState === "pairing_failed_terminal"
              ? "운영자 연결 실패"
              : operatorPairingPending
              ? "운영자 기기 연결 중"
              : guestJoinPending
              ? "초대 확인 중"
              : guestSession?.sessionToken
              ? "게스트로 접속"
              : "읽기 전용 미리보기",
            expired: guestExpired,
          }
        : undefined,
    [
      guestExpired,
      guestJoinPending,
      guestLocked,
      guestSession,
      operatorPairingPending,
      operatorPairingState,
    ]
  );

  const expireGuestSession = useCallback(() => {
    persistRoomGuestSession(null);
    dispatchAdmission({ type: "expired", status: GUEST_SESSION_EXPIRED_MESSAGE });
    onResetToLobby();
  }, [onResetToLobby]);

  const clearGuestSession = useCallback(() => {
    persistRoomGuestSession(null);
    dispatchAdmission({ type: "session_cleared" });
  }, []);

  const requestGuestJoin = useCallback(() => {
    dispatchAdmission({ type: "join_requested", status: "" });
  }, []);

  const retryOperatorPairing = useCallback(() => {
    if (operatorPairingState !== "pairing_failed_retryable") return;
    pairingAttemptedTokenRef.current = "";
    dispatchAdmission({
      type: "pairing_started",
      status: "공개 주소의 운영자 신원을 연결하는 중...",
    });
    setOperatorPairingAttempt((attempt) => attempt + 1);
  }, [operatorPairingState]);

  const clearInviteUrl = useCallback(() => {
    try {
      window.history.replaceState({}, "", window.location.pathname || "/join");
    } catch {
      // URL cleanup is best-effort; verified session state remains authoritative.
    }
  }, []);

  const applyJoinedSession = useCallback(
    (
      inviteToken: string,
      payload: RoomInviteJoinResponse,
      avatarImage: string,
      source: AdmissionSource
    ) => {
      const nextSession = roomGuestSessionFromJoinPayload(inviteToken, {
        ...payload,
        avatar_image_url: payload.avatar_image_url || avatarImage,
      });
      persistRoomGuestSession(nextSession);
      rememberGuestProfile({
        displayName: nextSession.displayName || pendingGuestDisplayName,
        avatarImage: nextSession.avatarImage || avatarImage || undefined,
      });
      dispatchAdmission({ type: "joined", session: nextSession, source });
      onRoomJoined(roomFromGuestSession(nextSession));
      clearInviteUrl();
    },
    [clearInviteUrl, onRoomJoined, pendingGuestDisplayName]
  );

  const acceptRecoveredSession = useCallback(
    (payload: RoomInviteJoinResponse) => {
      applyJoinedSession("", payload, payload.avatar_image_url || "", "recovery");
    },
    [applyJoinedSession]
  );

  useEffect(() => {
    if (!operatorPairingToken || admissionState.kind !== "pairing") return;
    if (pairingAttemptedTokenRef.current === operatorPairingToken) return;
    pairingAttemptedTokenRef.current = operatorPairingToken;
    let cancelled = false;
    dispatchAdmission({
      type: "pairing_started",
      status: "공개 주소의 운영자 신원을 연결하는 중...",
    });
    redeemOperatorPairing({
      pairingToken: operatorPairingToken,
      deviceToken: getOrCreateDeviceToken(),
    })
      .then((payload) => {
        if (cancelled) return;
        onPairingTokenConsumedRef.current();
        applyJoinedSession("", payload, payload.avatar_image_url || "", "pairing");
      })
      .catch((error) => {
        if (cancelled) return;
        const retryable = pairingFailureIsRetryable(error);
        if (!retryable) onPairingTokenConsumedRef.current();
        const message = error instanceof Error ? error.message : "운영자 기기 연결 실패";
        dispatchAdmission({
          type: "failed",
          operation: "pairing",
          code: error instanceof ApiError ? error.message : "pairing_failed",
          message,
          retryable,
          status: retryable
            ? `${message} 다시 시도할 수 있습니다.`
            : `${message} 이 연결 링크는 사용할 수 없습니다. 호스트에게 새 링크를 요청하세요.`,
        });
      });
    return () => {
      cancelled = true;
    };
  }, [
    admissionState.kind,
    applyJoinedSession,
    operatorPairingAttempt,
    operatorPairingToken,
  ]);

  useEffect(() => {
    if (
      !guestJoinToken ||
      operatorPairingToken ||
      admissionState.kind !== "preflighting"
    ) {
      return;
    }
    if (preflightAttemptedTokenRef.current === guestJoinToken) return;
    preflightAttemptedTokenRef.current = guestJoinToken;
    let cancelled = false;
    dispatchAdmission({
      type: "preflight_started",
      status: "초대와 기존 신원을 확인하는 중...",
    });
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
          dispatchAdmission({
            type: "joined",
            session: preservedSession,
            source: "existing_session",
          });
          onRoomJoined(roomFromGuestSession(preservedSession));
          clearAdmissionRequestId();
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
          dispatchAdmission({ type: "join_requested", status: "" });
          return;
        }
        if (decision.status === "profile_required") {
          const remembered = loadRememberedGuestProfile();
          if (remembered) {
            setPendingGuestDisplayName(remembered.displayName);
            setPendingGuestAvatarImage(remembered.avatarImage || "");
          }
          dispatchAdmission({ type: "profile_required" });
          return;
        }
        const message =
          decision.status === "invite_expired"
            ? "초대 링크가 만료되었습니다."
            : decision.status === "agent_client_required"
              ? "이 링크는 에이전트 세션 전용입니다. 터미널에서 AgentsAssemble 참가 명령으로 연결하세요."
              : decision.reason || "유효하지 않은 초대 링크입니다.";
        dispatchAdmission({
          type: "failed",
          operation: "preflight",
          code: decision.status,
          message,
          retryable: false,
          status: message,
        });
      })
      .catch((error) => {
        if (cancelled) return;
        const message = error instanceof Error ? error.message : "초대 확인 실패";
        dispatchAdmission({
          type: "failed",
          operation: "preflight",
          code: error instanceof ApiError ? error.message : "preflight_failed",
          message,
          retryable: pairingFailureIsRetryable(error),
          status: message,
        });
      });
    return () => {
      cancelled = true;
    };
  }, [
    admissionState.kind,
    clearInviteUrl,
    guestJoinToken,
    guestSession,
    onRoomJoined,
    operatorPairingToken,
  ]);

  useEffect(() => {
    if (!guestJoinToken || guestAlreadyJoinedThisInvite) return;
    if (admissionState.kind !== "joining") return;
    let cancelled = false;
    dispatchAdmission({
      type: "join_requested",
      status: "초대 링크로 방에 입장 중...",
    });
    let requestId = "";
    try {
      requestId = loadOrCreateAdmissionRequestId();
    } catch (error) {
      const message =
        error instanceof Error ? error.message : "안전한 입장 요청을 만들 수 없습니다.";
      dispatchAdmission({
        type: "failed",
        operation: "join",
        code: "request_id_unavailable",
        message,
        retryable: true,
        status: message,
      });
      return;
    }
    joinRoomInvite({
      inviteToken: guestJoinToken,
      requestId,
      displayName: pendingGuestDisplayName,
      avatarImage: pendingGuestAvatarImage,
      deviceToken: getOrCreateDeviceToken(),
      clientId: getOrCreateClientId(),
      participantType: "human",
    })
      .then((payload) => {
        if (!cancelled) {
          clearAdmissionRequestId();
          applyJoinedSession(guestJoinToken, payload, pendingGuestAvatarImage, "invite");
        }
      })
      .catch((error) => {
        if (cancelled) return;
        const restoredSession = loadRoomGuestSession();
        if (restoredSession?.inviteToken === guestJoinToken) {
          dispatchAdmission({
            type: "joined",
            session: restoredSession,
            source: "restored",
          });
          onRoomJoined(roomFromGuestSession(restoredSession));
          clearAdmissionRequestId();
          clearInviteUrl();
          return;
        }
        const message = error instanceof Error ? error.message : "초대 링크 입장 실패";
        dispatchAdmission({
          type: "failed",
          operation: "join",
          code: error instanceof ApiError ? error.message : "join_failed",
          message,
          retryable: true,
          status: message,
        });
      });
    return () => {
      cancelled = true;
    };
  }, [
    admissionState.kind,
    applyJoinedSession,
    clearInviteUrl,
    guestAlreadyJoinedThisInvite,
    guestJoinToken,
    onRoomJoined,
    pendingGuestAvatarImage,
    pendingGuestDisplayName,
  ]);

  return {
    admissionState,
    guestSession,
    admittedSessionToken,
    guestExpired,
    guestJoinRequested,
    pendingGuestDisplayName,
    pendingGuestAvatarImage,
    guestJoinStatus,
    guestAdmissionBusy,
    guestLocked,
    guestMeetingId,
    guestJoinPending,
    operatorPairingPending,
    operatorPairingState,
    guestReadOnly,
    guestPanelProfile,
    setPendingGuestDisplayName,
    setPendingGuestAvatarImage,
    requestGuestJoin,
    retryOperatorPairing,
    acceptRecoveredSession,
    expireGuestSession,
    clearGuestSession,
  };
}
