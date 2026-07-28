import { useCallback, useEffect, useRef, useState } from "react";
import { Hash, Mic, MicOff, PhoneOff, Send, Volume2 } from "lucide-react";
import {
  fetchChannelLobby,
  fetchVoicePresence,
  joinVoiceChannel,
  leaveVoiceChannel,
  postChannelSay,
  type LobbyEvent,
  type RoomChannel,
  type VoiceParticipant,
} from "../api";
import { usePoll } from "../hooks";
import ChannelHeader from "./components/ChannelHeader";
import type { ChannelHeaderActions } from "./components/ChannelHeader";

/**
 * A custom (user-created) channel: a text channel renders its own message
 * stream + composer (poll-based, like the lobby's HTTP fallback); a voice
 * channel renders live presence with join/leave (audio streaming deferred).
 * Dual-mode auth: a guest passes its session token; the local operator console
 * passes the room id + display name and rides the loopback path.
 */
export default function CustomChannelView({
  channel,
  meetingId,
  sessionToken,
  localDisplayName,
  canPost,
  membersOpen,
  onToggleMembers,
  onOpenMobileSidebar,
  onOpenMobileInfo,
  headerActions,
}: {
  channel: RoomChannel;
  meetingId: string;
  sessionToken: string;
  localDisplayName: string;
  canPost: boolean;
  membersOpen?: boolean;
  onToggleMembers?: () => void;
  onOpenMobileSidebar?: () => void;
  onOpenMobileInfo?: () => void;
  headerActions?: ChannelHeaderActions;
}) {
  if (channel.type === "voice") {
    return (
      <VoiceChannelBody
        channel={channel}
        meetingId={meetingId}
        sessionToken={sessionToken}
        localDisplayName={localDisplayName}
        canJoin={canPost}
        membersOpen={membersOpen}
        onToggleMembers={onToggleMembers}
        onOpenMobileSidebar={onOpenMobileSidebar}
        onOpenMobileInfo={onOpenMobileInfo}
        headerActions={headerActions}
      />
    );
  }
  return (
    <TextChannelBody
      channel={channel}
      meetingId={meetingId}
      sessionToken={sessionToken}
      localDisplayName={localDisplayName}
      canPost={canPost}
      membersOpen={membersOpen}
      onToggleMembers={onToggleMembers}
      onOpenMobileSidebar={onOpenMobileSidebar}
      onOpenMobileInfo={onOpenMobileInfo}
      headerActions={headerActions}
    />
  );
}

function TextChannelBody({
  channel,
  meetingId,
  sessionToken,
  localDisplayName,
  canPost,
  membersOpen,
  onToggleMembers,
  onOpenMobileSidebar,
  onOpenMobileInfo,
  headerActions,
}: {
  channel: RoomChannel;
  meetingId: string;
  sessionToken: string;
  localDisplayName: string;
  canPost: boolean;
  membersOpen?: boolean;
  onToggleMembers?: () => void;
  onOpenMobileSidebar?: () => void;
  onOpenMobileInfo?: () => void;
  headerActions?: ChannelHeaderActions;
}) {
  const [draft, setDraft] = useState("");
  const [sendError, setSendError] = useState("");
  const [sending, setSending] = useState(false);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  const fetcher = useCallback(
    () => fetchChannelLobby(channel.id, { sessionToken: sessionToken || undefined, meetingId }),
    [channel.id, sessionToken, meetingId]
  );
  const [events, , error, refresh] = usePoll<LobbyEvent[]>(fetcher, 2500);

  useEffect(() => {
    const node = scrollRef.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [events]);

  async function send() {
    const message = draft.trim();
    if (!message || sending) return;
    setSending(true);
    setSendError("");
    try {
      await postChannelSay({
        channelId: channel.id,
        message,
        sessionToken: sessionToken || undefined,
        meetingId,
        name: localDisplayName || undefined,
      });
      setDraft("");
      refresh();
    } catch (err) {
      setSendError(err instanceof Error ? err.message : "메시지를 보내지 못했습니다");
    } finally {
      setSending(false);
    }
  }

  const messages = events || [];

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col">
      <ChannelHeader
        icon={<Hash size={18} />}
        title={channel.name}
        subtitle="커스텀 텍스트 채널"
        membersOpen={membersOpen}
        onToggleMembers={onToggleMembers}
        onOpenMobileSidebar={onOpenMobileSidebar}
        onOpenMobileInfo={onOpenMobileInfo}
        headerActions={headerActions}
      />
      <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto px-4 py-3 chat-scroll">
        {error && !messages.length ? (
          <p className="text-[13px] text-text-muted">채널을 불러오지 못했습니다.</p>
        ) : !messages.length ? (
          <p className="text-[13px] text-text-muted">
            #{channel.name} 채널의 첫 메시지를 남겨보세요.
          </p>
        ) : (
          <ul className="dc-channel-message-list">
            {messages.map((event) => (
              <li key={event.id} className="dc-channel-message">
                <span className="dc-channel-message-author preserve-words">
                  {event.name || "익명"}
                </span>
                <span className="dc-channel-message-body preserve-words">{event.message}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
      <div className="dc-channel-composer">
        <textarea
          className="ops-input dc-channel-composer-input"
          value={draft}
          rows={1}
          placeholder={canPost ? `#${channel.name}에 메시지 보내기` : "이 채널에 글을 쓸 수 없습니다"}
          disabled={!canPost || sending}
          onChange={(event) => setDraft(event.target.value.slice(0, 2000))}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              void send();
            }
          }}
        />
        <button
          type="button"
          className="ops-cta dc-channel-composer-send"
          disabled={!canPost || sending || !draft.trim()}
          onClick={() => void send()}
          aria-label="보내기"
        >
          <Send size={16} />
        </button>
      </div>
      {sendError && <p className="dc-channel-composer-error preserve-words">{sendError}</p>}
    </div>
  );
}

function VoiceChannelBody({
  channel,
  meetingId,
  sessionToken,
  localDisplayName,
  canJoin,
  membersOpen,
  onToggleMembers,
  onOpenMobileSidebar,
  onOpenMobileInfo,
  headerActions,
}: {
  channel: RoomChannel;
  meetingId: string;
  sessionToken: string;
  localDisplayName: string;
  canJoin: boolean;
  membersOpen?: boolean;
  onToggleMembers?: () => void;
  onOpenMobileSidebar?: () => void;
  onOpenMobileInfo?: () => void;
  headerActions?: ChannelHeaderActions;
}) {
  const [connected, setConnected] = useState(false);
  const [selfMuted, setSelfMuted] = useState(false);
  const [actionError, setActionError] = useState("");
  const activeConnectionRef = useRef<Parameters<typeof leaveVoiceChannel>[0] | null>(null);

  const tokenOpt = sessionToken || undefined;
  const presenceFetcher = useCallback(
    () => fetchVoicePresence(channel.id, { sessionToken: tokenOpt, meetingId }),
    [channel.id, tokenOpt, meetingId]
  );
  const [participants, , , refresh] = usePoll<VoiceParticipant[]>(presenceFetcher, 5000);

  // Heartbeat: while connected, re-post join so presence does not time out.
  useEffect(() => {
    if (!connected) return;
    const beat = () => {
      void joinVoiceChannel({
        channelId: channel.id,
        sessionToken: tokenOpt,
        meetingId,
        name: localDisplayName || undefined,
        muted: selfMuted,
      })
        .then(() => refresh())
        .catch((err) => {
          setActionError(err instanceof Error ? err.message : "음성 채널 연결을 유지하지 못했습니다");
        });
    };
    const id = window.setInterval(beat, 20000);
    return () => window.clearInterval(id);
  }, [connected, channel.id, tokenOpt, meetingId, localDisplayName, selfMuted, refresh]);

  // The ref owns the exact successful join identity. Render state is not a
  // reliable cleanup source because an effect cleanup closes over an earlier
  // render, and the room/channel identity can change before it runs.
  useEffect(() => {
    setConnected(false);
    setSelfMuted(false);
    setActionError("");
    return () => {
      const connection = activeConnectionRef.current;
      activeConnectionRef.current = null;
      if (connection) void leaveVoiceChannel(connection);
    };
  }, [channel.id, meetingId, tokenOpt]);

  async function toggleConnected() {
    setActionError("");
    try {
      if (connected) {
        const connection = activeConnectionRef.current || {
          channelId: channel.id,
          sessionToken: tokenOpt,
          meetingId,
        };
        await leaveVoiceChannel(connection);
        activeConnectionRef.current = null;
        setConnected(false);
      } else {
        await joinVoiceChannel({
          channelId: channel.id,
          sessionToken: tokenOpt,
          meetingId,
          name: localDisplayName || undefined,
          muted: selfMuted,
        });
        activeConnectionRef.current = {
          channelId: channel.id,
          sessionToken: tokenOpt,
          meetingId,
        };
        setConnected(true);
      }
      refresh();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "음성 채널 작업에 실패했습니다");
    }
  }

  const members = participants || [];

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col">
      <ChannelHeader
        icon={<Volume2 size={18} />}
        title={channel.name}
        subtitle="음성 채널 (오디오는 준비 중 · 현재는 접속/프레즌스)"
        membersOpen={membersOpen}
        onToggleMembers={onToggleMembers}
        onOpenMobileSidebar={onOpenMobileSidebar}
        onOpenMobileInfo={onOpenMobileInfo}
        headerActions={headerActions}
      />
      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4 chat-scroll">
        <div className="dc-voice-stage">
          {members.length ? (
            <ul className="dc-voice-roster">
              {members.map((member) => (
                <li key={member.participantId} className="dc-voice-tile" data-muted={member.muted}>
                  <span className="dc-voice-avatar">{(member.name || "?").slice(0, 1).toUpperCase()}</span>
                  <span className="dc-voice-name preserve-words">{member.name}</span>
                  {member.muted && <MicOff size={13} className="opacity-70" />}
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-[13px] text-text-muted">아직 아무도 음성 채널에 없습니다.</p>
          )}
        </div>
      </div>
      <div className="dc-voice-controls">
        <button
          type="button"
          className="ops-cta"
          data-tone={connected ? "danger" : undefined}
          disabled={!canJoin}
          onClick={() => void toggleConnected()}
        >
          {connected ? <PhoneOff size={16} /> : <Volume2 size={16} />}
          {connected ? "나가기" : "음성 참여"}
        </button>
        {connected && (
          <button
            type="button"
            className="ops-cta"
            data-active={selfMuted}
            onClick={() => setSelfMuted((muted) => !muted)}
            aria-pressed={selfMuted}
          >
            {selfMuted ? <MicOff size={16} /> : <Mic size={16} />}
            {selfMuted ? "음소거됨" : "마이크 켜짐"}
          </button>
        )}
        {actionError && <span className="dc-channel-composer-error preserve-words">{actionError}</span>}
      </div>
    </div>
  );
}
