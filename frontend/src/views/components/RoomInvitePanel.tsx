import { useCallback, useEffect, useRef, useState } from "react";
import { Copy, LogIn, LogOut, Send, UserPlus, Users } from "lucide-react";
import {
  createRoomInvite,
  fetchRoomLobby,
  joinRoomWithInvite,
  leaveRoom,
  postRoomMessage,
  subscribeRoomEvents,
  type LobbyEvent,
  type RoomInvite,
} from "../../api";

// --- Host: Create Invite ---

function InviteCreator({ meetingId }: { meetingId: string }) {
  const [invite, setInvite] = useState<RoomInvite | null>(null);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState("");

  async function handleCreate() {
    setBusy(true);
    setError("");
    try {
      const result = await createRoomInvite({ meeting_id: meetingId });
      setInvite(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : "생성 실패");
    } finally {
      setBusy(false);
    }
  }

  function handleCopy() {
    if (!invite) return;
    navigator.clipboard.writeText(invite.invite_token).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <UserPlus size={16} className="text-accent" />
        <h3 className="text-[13px] font-black text-text-primary">초대 생성</h3>
      </div>
      <p className="text-[11px] text-text-muted">
        이 방에 참여할 수 있는 일회용 초대 토큰을 생성합니다.
      </p>
      <button
        type="button"
        onClick={handleCreate}
        disabled={busy || !meetingId}
        className="w-full rounded-lg border border-accent/40 bg-accent/10 px-3 py-2 text-[12px] font-bold text-accent transition hover:bg-accent/20 disabled:opacity-50"
      >
        {busy ? "생성 중..." : "초대 토큰 생성"}
      </button>
      {error && (
        <p className="rounded border border-danger/30 bg-danger/10 px-2 py-1.5 text-[11px] text-danger">
          {error}
        </p>
      )}
      {invite && (
        <div className="space-y-2 rounded-lg border border-online/30 bg-online/5 p-3">
          <div className="flex items-center justify-between gap-2">
            <span className="text-[11px] font-bold text-online">토큰 생성됨</span>
            <span className="text-[10px] text-text-muted">
              {invite.agent_id}
            </span>
          </div>
          <div className="flex items-center gap-2">
            <code className="min-w-0 flex-1 truncate rounded bg-black/30 px-2 py-1.5 font-mono text-[10px] text-text-secondary">
              {invite.invite_token}
            </code>
            <button
              type="button"
              onClick={handleCopy}
              className="shrink-0 rounded border border-line/60 bg-panel/50 p-1.5 text-text-muted transition hover:text-accent"
              title="복사"
            >
              <Copy size={13} />
            </button>
          </div>
          {copied && (
            <p className="text-[10px] font-bold text-online">클립보드에 복사됨</p>
          )}
          <p className="text-[10px] text-text-muted">
            이 토큰을 참여자에게 전달하세요. 일회용이며 만료됩니다.
          </p>
        </div>
      )}
    </div>
  );
}

// --- Guest: Join Room ---

function GuestJoinForm({
  onJoined,
}: {
  onJoined: (session: { token: string; agentId: string; displayName: string; meetingId: string }) => void;
}) {
  const [token, setToken] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function handleJoin() {
    if (!token.trim()) return;
    setBusy(true);
    setError("");
    try {
      const result = await joinRoomWithInvite({
        invite_token: token.trim(),
        display_name: displayName.trim() || undefined,
      });
      if (result.status === "admitted" && result.session_token) {
        onJoined({
          token: result.session_token,
          agentId: result.agent_id || "",
          displayName: result.display_name || "",
          meetingId: result.meeting_id || "",
        });
      } else {
        setError(result.reason || "입장 거부됨");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "입장 실패");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <LogIn size={16} className="text-accent" />
        <h3 className="text-[13px] font-black text-text-primary">방 입장</h3>
      </div>
      <label className="grid gap-1 text-[11px] font-bold text-text-muted">
        초대 토큰
        <input
          className="rounded border border-line/70 bg-black/20 px-3 py-2 font-mono text-[11px] text-text-primary outline-none transition focus:border-accent focus:ring-1 focus:ring-accent/30"
          value={token}
          onChange={(e) => setToken(e.target.value)}
          placeholder="aai1...."
          spellCheck={false}
        />
      </label>
      <label className="grid gap-1 text-[11px] font-bold text-text-muted">
        표시 이름 (선택)
        <input
          className="rounded border border-line/70 bg-black/20 px-3 py-2 text-[12px] text-text-primary outline-none transition focus:border-accent focus:ring-1 focus:ring-accent/30"
          value={displayName}
          onChange={(e) => setDisplayName(e.target.value)}
          placeholder="Guest"
          spellCheck={false}
        />
      </label>
      <button
        type="button"
        onClick={handleJoin}
        disabled={busy || !token.trim()}
        className="w-full rounded-lg border border-accent/40 bg-accent/10 px-3 py-2 text-[12px] font-bold text-accent transition hover:bg-accent/20 disabled:opacity-50"
      >
        {busy ? "입장 중..." : "입장"}
      </button>
      {error && (
        <p className="rounded border border-danger/30 bg-danger/10 px-2 py-1.5 text-[11px] text-danger">
          {error}
        </p>
      )}
    </div>
  );
}

// --- Guest: Room Chat (after join) ---

function GuestRoomChat({
  sessionToken,
  agentId,
  displayName,
  onLeave,
}: {
  sessionToken: string;
  agentId: string;
  displayName: string;
  onLeave: () => void;
}) {
  const [events, setEvents] = useState<LobbyEvent[]>([]);
  const [message, setMessage] = useState("");
  const [sending, setSending] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    fetchRoomLobby(sessionToken)
      .then((data) => setEvents(data.events || []))
      .catch(() => {});
  }, [sessionToken]);

  useEffect(() => {
    return subscribeRoomEvents(sessionToken, (incoming) => {
      setEvents((prev) => {
        const ids = new Set(prev.map((e) => e.id));
        const fresh = incoming.filter((e) => !ids.has(e.id));
        return fresh.length ? [...prev, ...fresh] : prev;
      });
    });
  }, [sessionToken]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [events.length]);

  async function handleSend() {
    if (!message.trim()) return;
    setSending(true);
    try {
      await postRoomMessage(sessionToken, { message: message.trim() });
      setMessage("");
    } catch {
      // best-effort
    } finally {
      setSending(false);
    }
  }

  async function handleLeave() {
    await leaveRoom(sessionToken).catch(() => {});
    onLeave();
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between border-b border-line/40 px-3 py-2">
        <div className="flex items-center gap-2">
          <Users size={14} className="text-accent" />
          <span className="text-[12px] font-bold text-text-primary">{displayName}</span>
          <span className="text-[10px] text-text-muted">({agentId})</span>
        </div>
        <button
          type="button"
          onClick={handleLeave}
          className="flex items-center gap-1 rounded border border-danger/30 bg-danger/10 px-2 py-1 text-[10px] font-bold text-danger transition hover:bg-danger/20"
        >
          <LogOut size={11} />
          나가기
        </button>
      </div>
      <div
        ref={scrollRef}
        className="flex-1 space-y-1 overflow-y-auto p-3 chat-scroll"
        style={{ maxHeight: "280px" }}
      >
        {events.length === 0 ? (
          <p className="text-[11px] text-text-muted">아직 메시지가 없습니다.</p>
        ) : (
          events.slice(-50).map((event) => (
            <div key={event.id} className="text-[12px] leading-relaxed">
              <span className="font-bold text-text-primary">{event.name || "Room"}</span>{" "}
              <span className="text-text-secondary preserve-words">{event.message}</span>
            </div>
          ))
        )}
      </div>
      <div className="flex items-center gap-2 border-t border-line/40 p-2">
        <input
          className="min-w-0 flex-1 rounded border border-line/60 bg-black/20 px-3 py-1.5 text-[12px] text-text-primary outline-none transition focus:border-accent"
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleSend(); } }}
          placeholder="메시지 입력..."
          spellCheck={false}
        />
        <button
          type="button"
          onClick={handleSend}
          disabled={sending || !message.trim()}
          className="rounded border border-accent/40 bg-accent/10 p-1.5 text-accent transition hover:bg-accent/20 disabled:opacity-50"
        >
          <Send size={14} />
        </button>
      </div>
    </div>
  );
}

// --- Main Export ---

export default function RoomInvitePanel({ meetingId }: { meetingId: string }) {
  const [mode, setMode] = useState<"host" | "guest">("host");
  const [session, setSession] = useState<{
    token: string;
    agentId: string;
    displayName: string;
    meetingId: string;
  } | null>(null);

  const handleJoined = useCallback(
    (s: { token: string; agentId: string; displayName: string; meetingId: string }) => {
      setSession(s);
    },
    []
  );

  if (session) {
    return (
      <GuestRoomChat
        sessionToken={session.token}
        agentId={session.agentId}
        displayName={session.displayName}
        onLeave={() => setSession(null)}
      />
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex gap-1 rounded-lg border border-line/50 bg-black/20 p-1">
        <button
          type="button"
          onClick={() => setMode("host")}
          className={`flex-1 rounded-md px-3 py-1.5 text-[11px] font-bold transition ${
            mode === "host"
              ? "bg-accent/15 text-accent"
              : "text-text-muted hover:text-text-secondary"
          }`}
        >
          호스트
        </button>
        <button
          type="button"
          onClick={() => setMode("guest")}
          className={`flex-1 rounded-md px-3 py-1.5 text-[11px] font-bold transition ${
            mode === "guest"
              ? "bg-accent/15 text-accent"
              : "text-text-muted hover:text-text-secondary"
          }`}
        >
          게스트
        </button>
      </div>
      {mode === "host" ? (
        <InviteCreator meetingId={meetingId} />
      ) : (
        <GuestJoinForm onJoined={handleJoined} />
      )}
    </div>
  );
}
