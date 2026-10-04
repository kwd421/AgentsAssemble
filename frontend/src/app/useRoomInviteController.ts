import { useEffect, useState } from "react";
import {
  claimHostDevice,
  clearHostToken,
  configurePublicInvitePublicUrl,
  createOperatorPairing,
  createRoomInvite,
  fetchPublicInviteStatus,
  generatePublicInviteHostToken,
  loadHostToken,
  saveHostToken,
  startPublicInviteTunnel,
  stopPublicInviteTunnel,
  type PublicInviteStatus,
  type RoomFriend,
} from "../api";
import { getOrCreateDeviceToken } from "../lib/deviceIdentity";
import {
  remoteClientPacketPreview,
  secureInviteCopyTarget,
} from "../lib/roomInviteCopy";
import { localPreviewInviteUrlForRoom, type RoomDockItem } from "../lib/roomDockModel";
import type { RoomAppearance } from "../lib/roomAppearance";

type InviteModalState = { roomId: string } | null;

type InviteRemoteClientPacketState = {
  friendName: string;
  preview: string;
};

export type PublicAccessTransition = "idle" | "starting" | "stopping";

export type HumanInviteOptions = {
  maxUses: number;
  ttlSeconds: number;
};

type UseRoomInviteControllerOptions = {
  guestLocked: boolean;
  sessionToken?: string;
};

async function copyText(value: string) {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(value);
      return true;
    }
  } catch {
    // Fall through when browser permissions reject clipboard writes.
  }
  const textarea = document.createElement("textarea");
  textarea.value = value;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.left = "-9999px";
  textarea.style.top = "0";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  textarea.focus({ preventScroll: true });
  textarea.select();
  textarea.setSelectionRange(0, value.length);
  const copied = document.execCommand("copy");
  textarea.remove();
  return copied;
}

function inviteErrorLooksLikeHostToken(error: unknown) {
  const message = error instanceof Error ? error.message.toLowerCase() : "";
  return message.includes("host token") || message.includes("forbidden");
}

export function useRoomInviteController({
  guestLocked,
  sessionToken = "",
}: UseRoomInviteControllerOptions) {
  const [modal, setModal] = useState<InviteModalState>(null);
  const [copyStatus, setCopyStatus] = useState("");
  const [secureInviteUrl, setSecureInviteUrl] = useState("");
  const [agentInviteUrl, setAgentInviteUrl] = useState("");
  const [operatorPairingUrl, setOperatorPairingUrl] = useState("");
  const [publicInviteStatus, setPublicInviteStatus] = useState<PublicInviteStatus | null>(null);
  const [publicUrlDraft, setPublicUrlDraft] = useState("");
  const [hostTokenDraft, setHostTokenDraft] = useState("");
  const [publicAccessTransition, setPublicAccessTransition] =
    useState<PublicAccessTransition>("idle");
  const [friendStatuses, setFriendStatuses] = useState<Record<string, string>>({});
  const [remoteClientPacket, setRemoteClientPacket] =
    useState<InviteRemoteClientPacketState>({ friendName: "", preview: "" });

  function open(roomId: string) {
    setModal({ roomId });
    setCopyStatus("");
    setSecureInviteUrl("");
    setAgentInviteUrl("");
    setOperatorPairingUrl("");
    setHostTokenDraft(loadHostToken());
    setPublicAccessTransition("idle");
    setFriendStatuses({});
    setRemoteClientPacket({ friendName: "", preview: "" });
  }

  function close() {
    setModal(null);
  }

  useEffect(() => {
    if (!modal) return;
    let cancelled = false;
    setSecureInviteUrl("");
    setCopyStatus("");
    setHostTokenDraft(loadHostToken());
    fetchPublicInviteStatus()
      .then((status) => {
        if (cancelled) return;
        setPublicInviteStatus(status);
        setPublicUrlDraft(status.public_url || status.tunnel?.public_url || "");
      })
      .catch((error) => {
        if (!cancelled) {
          setCopyStatus(error instanceof Error ? error.message : "공개 초대 상태를 불러오지 못했습니다.");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [modal?.roomId]);

  useEffect(() => {
    if (guestLocked) return;
    let cancelled = false;
    void (async () => {
      try {
        if (!loadHostToken()) {
          const status = await fetchPublicInviteStatus();
          if (cancelled) return;
          setPublicInviteStatus(status);
          if (status.host_token_configured || status.can_generate_host_token) {
            await ensureHostToken(status);
          }
        }
        if (!cancelled && loadHostToken()) {
          await claimHostDevice({ deviceToken: getOrCreateDeviceToken() });
        }
      } catch {
        // Moderation actions report a concrete error if bootstrap did not succeed.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [guestLocked]);

  async function refreshPublicInviteState() {
    const status = await fetchPublicInviteStatus();
    setPublicInviteStatus(status);
    if (status.public_url || status.tunnel?.public_url) {
      setPublicUrlDraft(status.public_url || status.tunnel?.public_url || "");
    }
    return status;
  }

  async function ensureHostToken(status: PublicInviteStatus | null) {
    const existingToken = loadHostToken();
    if (existingToken) return existingToken;
    if (status && (!status.host_token_configured || status.can_generate_host_token)) {
      const payload = await generatePublicInviteHostToken();
      if (payload.host_token) {
        saveHostToken(payload.host_token);
        setHostTokenDraft(payload.host_token);
      }
      if (payload.public_invite) setPublicInviteStatus(payload.public_invite);
      return payload.host_token || "";
    }
    try {
      const payload = await generatePublicInviteHostToken();
      if (payload.host_token) {
        saveHostToken(payload.host_token);
        setHostTokenDraft(payload.host_token);
        if (payload.public_invite) setPublicInviteStatus(payload.public_invite);
        return payload.host_token;
      }
    } catch {
      // Existing operator-provided host tokens still require manual entry.
    }
    throw new Error("Host token required");
  }

  async function regenerateHostToken() {
    clearHostToken();
    setHostTokenDraft("");
    const status = await refreshPublicInviteState();
    const token = await ensureHostToken(status);
    if (!token) throw new Error("Host token required");
    return token;
  }

  async function waitForTunnelReady() {
    for (let attempt = 0; attempt < 18; attempt += 1) {
      const nextStatus = await refreshPublicInviteState();
      if (nextStatus.public_url && nextStatus.tunnel?.phase === "running") return nextStatus;
      if (nextStatus.tunnel?.phase === "stopped" || nextStatus.tunnel?.last_error) return nextStatus;
      await new Promise((resolve) => window.setTimeout(resolve, 1000));
    }
    return refreshPublicInviteState();
  }

  async function preparePublicInvite() {
    let status = await refreshPublicInviteState();
    if (!sessionToken) await ensureHostToken(status);
    if (status.public_url) return status;
    if (sessionToken) {
      throw new Error("공개 주소 세션에서는 로컬 터널을 시작할 수 없습니다.");
    }
    if (!status.tunnel?.available) {
      throw new Error("공개 URL을 만들 수 없습니다. cloudflared를 설치하거나 공개 URL을 입력하세요.");
    }
    setCopyStatus("공개 터널 준비 중...");
    let started;
    try {
      started = await startPublicInviteTunnel();
    } catch (error) {
      if (!inviteErrorLooksLikeHostToken(error)) throw error;
      await regenerateHostToken();
      started = await startPublicInviteTunnel();
    }
    if (started.public_invite) {
      setPublicInviteStatus(started.public_invite);
      status = started.public_invite;
    }
    if (status.public_url && status.tunnel?.phase === "running") return status;
    const readyStatus = await waitForTunnelReady();
    if (readyStatus.public_url && readyStatus.tunnel?.phase === "running") return readyStatus;
    throw new Error(
      readyStatus.tunnel?.last_error ||
        "공개 터널이 아직 초대 URL을 보고하지 않았습니다. 잠시 후 다시 눌러 주세요."
    );
  }

  async function requirePublicInviteReady(startTunnelIfNeeded = false) {
    const status = startTunnelIfNeeded
      ? await preparePublicInvite()
      : await refreshPublicInviteState();
    if (!status.public_url) {
      throw new Error("외부 접속을 먼저 열어 주세요.");
    }
    if (status.tunnel?.phase === "starting" && !status.tunnel.public_url) {
      throw new Error("터널 시작 중입니다. 공개 URL이 표시될 때까지 기다려 주세요.");
    }
    if (!sessionToken) await ensureHostToken(status);
    return status;
  }

  async function createSecureInviteForRoom({
    room,
    agentId,
    displayName,
    inviteScope,
    ttlSeconds = 86400,
    maxUses = 1,
    startTunnelIfNeeded = false,
  }: {
    room: RoomDockItem;
    agentId: string;
    displayName: string;
    inviteScope: RoomAppearance["inviteScope"];
    ttlSeconds?: number;
    maxUses?: number;
    startTunnelIfNeeded?: boolean;
  }) {
    await requirePublicInviteReady(startTunnelIfNeeded);
    const localPreviewUrl = localPreviewInviteUrlForRoom(room);
    let invite;
    try {
      invite = await createRoomInvite({
        meetingId: room.meetingId,
        agentId,
        displayName,
        inviteScope,
        ttlSeconds,
        maxUses,
        sessionToken,
      });
    } catch (error) {
      if (!inviteErrorLooksLikeHostToken(error)) throw error;
      await regenerateHostToken();
      invite = await createRoomInvite({
        meetingId: room.meetingId,
        agentId,
        displayName,
        inviteScope,
        ttlSeconds,
        maxUses,
        sessionToken,
      });
    }
    const target = secureInviteCopyTarget({ joinUrl: invite.join_url || "", localPreviewUrl });
    if (!target.copyUrl) throw new Error(target.status);
    setSecureInviteUrl(target.copyUrl);
    return { invite, target };
  }

  async function configurePublicUrl() {
    const publicUrl = publicUrlDraft.trim();
    if (!publicUrl) {
      setCopyStatus("공개 URL을 먼저 입력하세요.");
      return;
    }
    setCopyStatus("공개 URL 설정 중...");
    try {
      const status = publicInviteStatus || (await refreshPublicInviteState());
      await ensureHostToken(status);
      let payload;
      try {
        payload = await configurePublicInvitePublicUrl(publicUrl);
      } catch (error) {
        if (!inviteErrorLooksLikeHostToken(error)) throw error;
        await regenerateHostToken();
        payload = await configurePublicInvitePublicUrl(publicUrl);
      }
      if (payload.public_invite) setPublicInviteStatus(payload.public_invite);
      else await refreshPublicInviteState();
      setCopyStatus("공개 URL 설정됨");
    } catch (error) {
      setCopyStatus(error instanceof Error ? error.message : "공개 URL 설정 실패");
    }
  }

  async function saveHostTokenFromDraft() {
    const token = hostTokenDraft.trim();
    if (!token) {
      setCopyStatus("Host token required");
      return;
    }
    saveHostToken(token);
    setCopyStatus("Host token saved");
    try {
      await refreshPublicInviteState();
    } catch {
      // The saved credential remains useful when the status request is transiently unavailable.
    }
  }

  async function startTunnel() {
    setPublicAccessTransition("starting");
    setCopyStatus("외부 접속 주소를 준비하는 중...");
    try {
      const status = publicInviteStatus || (await refreshPublicInviteState());
      await ensureHostToken(status);
      let started;
      try {
        started = await startPublicInviteTunnel();
      } catch (error) {
        if (!inviteErrorLooksLikeHostToken(error)) throw error;
        await regenerateHostToken();
        started = await startPublicInviteTunnel();
      }
      if (started.host_token) {
        saveHostToken(started.host_token);
        setHostTokenDraft(started.host_token);
      }
      if (started.public_invite) setPublicInviteStatus(started.public_invite);
      const latest = await waitForTunnelReady();
      setCopyStatus(
        latest.public_url
          ? "서버가 공개되었습니다. 이제 외부 초대 링크를 만들 수 있습니다."
          : latest.tunnel?.last_error || "외부 접속 주소가 아직 준비되지 않았습니다."
      );
    } catch (error) {
      setCopyStatus(error instanceof Error ? error.message : "서버 공개 실패");
    } finally {
      setPublicAccessTransition("idle");
    }
  }

  async function stopTunnel() {
    setPublicAccessTransition("stopping");
    setCopyStatus("외부 접속을 닫는 중...");
    try {
      let payload = await stopPublicInviteTunnel();
      if (payload.public_invite) setPublicInviteStatus(payload.public_invite);
      if (payload.public_invite?.public_url) {
        payload = await configurePublicInvitePublicUrl("");
        if (payload.public_invite) setPublicInviteStatus(payload.public_invite);
      }
      if (!payload.public_invite) await refreshPublicInviteState();
      setPublicUrlDraft("");
      setSecureInviteUrl("");
      setAgentInviteUrl("");
      setOperatorPairingUrl("");
      setCopyStatus("외부 접속을 닫았습니다. 룸은 이 컴퓨터에서 계속 작동합니다.");
    } catch (error) {
      setCopyStatus(error instanceof Error ? error.message : "서버 비공개 전환 실패");
    } finally {
      setPublicAccessTransition("idle");
    }
  }

  async function generateSecureInvite(
    room: RoomDockItem,
    inviteScope: RoomAppearance["inviteScope"],
    options: HumanInviteOptions = { maxUses: 1, ttlSeconds: 86400 },
    startTunnelIfNeeded = false
  ) {
    setSecureInviteUrl("");
    setCopyStatus("보안 초대 링크 생성 중...");
    try {
      const { target } = await createSecureInviteForRoom({
        room,
        agentId: "guest",
        displayName: "Guest",
        inviteScope,
        ...options,
        startTunnelIfNeeded,
      });
      setCopyStatus(target.copyUrl ? "보안 초대 링크 생성됨" : target.status);
    } catch (error) {
      setCopyStatus(error instanceof Error ? error.message : "보안 초대 링크 생성 실패");
    }
  }

  async function generateAgentInvite(room: RoomDockItem, startTunnelIfNeeded = false) {
    setAgentInviteUrl("");
    setCopyStatus("외부 AI 세션 초대 링크 생성 중...");
    try {
      await requirePublicInviteReady(startTunnelIfNeeded);
      const invite = await createRoomInvite({
        meetingId: room.meetingId,
        agentId: "external-agent",
        displayName: "External Agent",
        inviteScope: "room",
        ttlSeconds: 3600,
        clientType: "browser",
        providerKind: "manual",
        participantType: "agent",
        maxUses: 1,
        sessionToken,
      });
      const target = secureInviteCopyTarget({
        joinUrl: invite.join_url || "",
        localPreviewUrl: localPreviewInviteUrlForRoom(room),
      });
      if (!target.copyUrl) throw new Error(target.status);
      setAgentInviteUrl(target.copyUrl);
      setCopyStatus("외부 AI 세션 1회용 초대 링크 생성됨");
    } catch (error) {
      setCopyStatus(error instanceof Error ? error.message : "외부 AI 세션 초대 생성 실패");
    }
  }

  async function copyAgentInvite() {
    if (!agentInviteUrl) return;
    const copied = await copyText(agentInviteUrl);
    setCopyStatus(copied ? "외부 AI 세션 초대 링크 복사됨" : "초대 링크 복사 실패");
  }

  async function generateOperatorPairing(room: RoomDockItem) {
    setCopyStatus("공개 주소용 운영자 연결 링크 생성 중...");
    try {
      await requirePublicInviteReady(false);
      const pairing = await createOperatorPairing({
        meetingId: room.meetingId,
        sessionToken,
      });
      setOperatorPairingUrl(pairing.pairing_url);
      setCopyStatus("운영자 연결 링크 생성됨 · 2분 안에 한 번만 사용할 수 있습니다.");
    } catch (error) {
      setCopyStatus(error instanceof Error ? error.message : "운영자 연결 링크 생성 실패");
    }
  }

  async function copyOperatorPairing() {
    if (!operatorPairingUrl) return;
    const copied = await copyText(operatorPairingUrl);
    setCopyStatus(copied ? "운영자 연결 링크 복사됨" : "운영자 연결 링크 복사 실패");
  }

  async function copySecureInvite(room: RoomDockItem) {
    const target = secureInviteCopyTarget({
      joinUrl: secureInviteUrl,
      localPreviewUrl: localPreviewInviteUrlForRoom(room),
    });
    if (!target.copyUrl) {
      setCopyStatus(target.status);
      return;
    }
    const copied = await copyText(target.copyUrl);
    setCopyStatus(copied ? target.status : "보안 초대 링크 복사 실패");
  }

  async function copyLocalPreview(room: RoomDockItem) {
    const copied = await copyText(localPreviewInviteUrlForRoom(room));
    setCopyStatus(copied ? "로컬 미리보기 복사됨" : "로컬 미리보기 복사 실패");
  }

  async function copyRemoteClientPacket() {
    if (!remoteClientPacket.preview) return;
    setCopyStatus("");
    const copied = await copyText(remoteClientPacket.preview);
    setCopyStatus(copied ? "AI 입장 패킷 복사됨" : "패킷 복사 실패");
  }

  async function inviteFriend({
    friend,
    room,
    appearance,
    startTunnelIfNeeded = false,
  }: {
    friend: RoomFriend;
    room: RoomDockItem;
    appearance?: RoomAppearance;
    startTunnelIfNeeded?: boolean;
  }) {
    const friendId = friend.friend_id;
    setFriendStatuses((previous) => ({ ...previous, [friendId]: "초대 중" }));
    try {
      const isAiFriend = friend.participant_type !== "human";
      const inviteScope = appearance?.inviteScope || room.inviteScope || "room";
      const participantId = friend.source_agent_id || friend.friend_id;
      const { invite } = await createSecureInviteForRoom({
        room,
        agentId: participantId,
        displayName: friend.display_name,
        inviteScope,
        startTunnelIfNeeded,
      });
      const packetPreview = isAiFriend
        ? remoteClientPacketPreview(invite.remote_client_packet)
        : "";
      setRemoteClientPacket({
        friendName: packetPreview ? friend.display_name : "",
        preview: packetPreview,
      });
      setFriendStatuses((previous) => ({
        ...previous,
        [friendId]: isAiFriend ? "입장 패킷 생성됨" : "초대 링크 생성됨",
      }));
    } catch (error) {
      setFriendStatuses((previous) => ({
        ...previous,
        [friendId]:
          error instanceof Error
            ? error.message
            : "초대 실패. 공개 URL과 host 권한 설정을 확인하세요.",
      }));
    }
  }

  return {
    modal,
    copyStatus,
    secureInviteUrl,
    agentInviteUrl,
    operatorPairingUrl,
    publicInviteStatus,
    publicAccessTransition,
    publicUrlDraft,
    hostTokenDraft,
    friendStatuses,
    remoteClientPacket,
    invitePublicUrl: publicInviteStatus?.public_url || publicInviteStatus?.tunnel?.public_url || "",
    hostTokenRequired: Boolean(publicInviteStatus?.host_token_configured && !loadHostToken()),
    open,
    close,
    setPublicUrlDraft,
    setHostTokenDraft,
    createSecureInviteForRoom,
    configurePublicUrl,
    saveHostTokenFromDraft,
    startTunnel,
    stopTunnel,
    generateSecureInvite,
    generateAgentInvite,
    generateOperatorPairing,
    copyAgentInvite,
    copyOperatorPairing,
    copySecureInvite,
    copyLocalPreview,
    copyRemoteClientPacket,
    inviteFriend,
  };
}
