import { useCallback, useEffect, useRef, useState } from "react";
import { Copy, Globe2, Link, LogIn, LogOut, Power, RefreshCw, Send, Shield, UserPlus, Users, X } from "lucide-react";
import {
  createRoomInvite,
  fetchPendingInvites,
  fetchPublicInviteStatus,
  fetchRoomLobby,
  generateHostToken,
  getHostToken,
  joinRoomWithInvite,
  leaveRoom,
  postRoomMessage,
  revokeRoomInvite,
  setPublicInviteUrl,
  setHostToken,
  startPublicInviteTunnel,
  stopPublicInviteTunnel,
  subscribeRoomEvents,
  type LobbyEvent,
  type PendingInvite,
  type PublicInviteStatus,
  type RoomInvite,
} from "../../api";

// --- Host Token Setup ---

function HostTokenGate({ children }: { children: React.ReactNode }) {
  const [token, setToken] = useState(getHostToken());
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function handleGenerateToken() {
    setBusy(true);
    setError("");
    try {
      const result = await generateHostToken();
      if (result.host_token) {
        setHostToken(result.host_token);
        setToken(result.host_token);
      } else {
        setError("이미 서버에 호스트 토큰이 설정되어 있습니다. 그 값을 입력하세요.");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "호스트 토큰 생성 실패");
    } finally {
      setBusy(false);
    }
  }

  if (token) {
    return (
      <div className="space-y-2">
        <div className="flex items-center gap-2 text-[11px] text-online">
          <Shield size={12} />
          <span className="font-bold">호스트 인증됨</span>
          <button
            type="button"
            onClick={() => { setHostToken(""); setToken(""); }}
            className="ml-auto text-[10px] text-text-muted hover:text-danger"
          >
            로그아웃
          </button>
        </div>
        {children}
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <Shield size={16} className="text-accent" />
        <h3 className="text-[13px] font-black text-text-primary">호스트 인증</h3>
      </div>
      <p className="text-[11px] text-text-muted">
        초대를 생성하려면 호스트 토큰이 필요합니다. 터미널 없이 시작하려면 새 토큰을 생성하세요.
      </p>
      <button
        type="button"
        onClick={handleGenerateToken}
        disabled={busy}
        className="w-full rounded-lg border border-online/40 bg-online/10 px-3 py-2 text-[11px] font-bold text-online transition hover:bg-online/20 disabled:opacity-50"
      >
        {busy ? "토큰 생성 중..." : "호스트 토큰 생성"}
      </button>
      {error && (
        <p className="rounded border border-danger/30 bg-danger/10 px-2 py-1.5 text-[11px] text-danger">
          {error}
        </p>
      )}
      <div className="flex items-center gap-2">
        <input
          className="min-w-0 flex-1 rounded border border-line/70 bg-black/20 px-3 py-2 font-mono text-[11px] text-text-primary outline-none transition focus:border-accent focus:ring-1 focus:ring-accent/30"
          type="password"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="AGENTSASSEMBLE_HOST_TOKEN"
          onKeyDown={(e) => {
            if (e.key === "Enter" && input.trim()) {
              setHostToken(input.trim());
              setToken(input.trim());
            }
          }}
        />
        <button
          type="button"
          onClick={() => { if (input.trim()) { setHostToken(input.trim()); setToken(input.trim()); } }}
          disabled={!input.trim()}
          className="rounded border border-accent/40 bg-accent/10 px-3 py-2 text-[11px] font-bold text-accent transition hover:bg-accent/20 disabled:opacity-50"
        >
          확인
        </button>
      </div>
    </div>
  );
}

// --- Host: Public URL / Tunnel Setup ---

function PublicInviteSetup() {
  const [status, setStatus] = useState<PublicInviteStatus | null>(null);
  const [manualUrl, setManualUrl] = useState("");
  const [busy, setBusy] = useState<"refresh" | "start" | "stop" | "manual" | "">("");
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    setError("");
    try {
      const next = await fetchPublicInviteStatus();
      setStatus(next);
      if (next.public_url && !manualUrl) setManualUrl(next.public_url);
    } catch (err) {
      setError(err instanceof Error ? err.message : "상태 확인 실패");
    }
  }, [manualUrl]);

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 3000);
    return () => clearInterval(interval);
  }, [refresh]);

  async function run(action: "start" | "stop" | "manual" | "refresh") {
    setBusy(action);
    setError("");
    try {
      if (action === "start") {
        const result = await startPublicInviteTunnel();
        setStatus(result.public_invite);
      } else if (action === "stop") {
        const result = await stopPublicInviteTunnel();
        setStatus(result.public_invite);
      } else if (action === "manual") {
        const result = await setPublicInviteUrl(manualUrl.trim());
        setStatus(result.public_invite);
      } else {
        await refresh();
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "공개 링크 설정 실패");
    } finally {
      setBusy("");
    }
  }

  const tunnel = status?.tunnel;
  const publicUrl = status?.public_url || tunnel?.public_url || "";
  const available = tunnel?.available !== false;
  const running = tunnel?.running === true;

  return (
    <div className="space-y-3 rounded-lg border border-line/50 bg-black/10 p-3">
      <div className="flex items-center gap-2">
        <Globe2 size={16} className={publicUrl ? "text-online" : "text-accent"} />
        <div className="min-w-0 flex-1">
          <h3 className="text-[13px] font-black text-text-primary">인터넷 초대 준비</h3>
          <p className="text-[10px] text-text-muted">
            공개 주소를 준비하면 친구에게 바로 보낼 링크가 생성됩니다.
          </p>
        </div>
        <button
          type="button"
          onClick={() => run("refresh")}
          disabled={!!busy}
          className="rounded border border-line/60 bg-panel/40 p-1.5 text-text-muted transition hover:text-accent disabled:opacity-50"
          title="새로고침"
        >
          <RefreshCw size={13} />
        </button>
      </div>

      {publicUrl ? (
        <div className="rounded border border-online/30 bg-online/5 px-2 py-1.5">
          <p className="text-[10px] font-bold text-online">공개 주소 준비됨</p>
          <code className="block truncate font-mono text-[10px] text-text-secondary">{publicUrl}</code>
        </div>
      ) : (
        <p className="rounded border border-accent/30 bg-accent/10 px-2 py-1.5 text-[11px] text-accent">
          아직 공개 주소가 없습니다. Cloudflare Tunnel을 시작하거나 이미 가진 공개 주소를 입력하세요.
        </p>
      )}

      <div className="grid gap-2 sm:grid-cols-2">
        <button
          type="button"
          onClick={() => run("start")}
          disabled={!!busy || running}
          className="flex items-center justify-center gap-2 rounded-lg border border-accent/40 bg-accent/10 px-3 py-2 text-[11px] font-bold text-accent transition hover:bg-accent/20 disabled:opacity-50"
        >
          <Power size={13} />
          {busy === "start" ? "시작 중..." : "공개 링크 열기"}
        </button>
        <button
          type="button"
          onClick={() => run("stop")}
          disabled={!!busy || !running}
          className="flex items-center justify-center gap-2 rounded-lg border border-danger/30 bg-danger/10 px-3 py-2 text-[11px] font-bold text-danger transition hover:bg-danger/20 disabled:opacity-50"
        >
          <X size={13} />
          공개 링크 닫기
        </button>
      </div>

      {!available && (
        <p className="rounded border border-warning/30 bg-warning/10 px-2 py-1.5 text-[10px] text-warning">
          cloudflared가 설치되어 있지 않아 버튼으로 터널을 열 수 없습니다. 설치 후 다시 누르거나 아래에 공개 주소를 입력하세요.
        </p>
      )}

      <div className="grid gap-2">
        <label className="grid gap-1 text-[11px] font-bold text-text-muted">
          공개 주소 직접 입력
          <input
            className="rounded border border-line/70 bg-black/20 px-3 py-2 font-mono text-[11px] text-text-primary outline-none transition focus:border-accent focus:ring-1 focus:ring-accent/30"
            value={manualUrl}
            onChange={(e) => setManualUrl(e.target.value)}
            placeholder="https://your-room.trycloudflare.com"
            spellCheck={false}
          />
        </label>
        <button
          type="button"
          onClick={() => run("manual")}
          disabled={!!busy || !manualUrl.trim()}
          className="rounded-lg border border-line/60 bg-panel/40 px-3 py-2 text-[11px] font-bold text-text-secondary transition hover:border-accent/40 hover:text-accent disabled:opacity-50"
        >
          {busy === "manual" ? "저장 중..." : "공개 주소 저장"}
        </button>
      </div>

      {(error || tunnel?.last_error) && (
        <p className="rounded border border-danger/30 bg-danger/10 px-2 py-1.5 text-[10px] text-danger">
          {error || tunnel?.last_error}
        </p>
      )}
    </div>
  );
}

// --- Host: Create Invite ---

function InviteCreator({ meetingId }: { meetingId: string }) {
  const [invite, setInvite] = useState<RoomInvite | null>(null);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState<"token" | "url" | "">("");
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

  function handleCopy(text: string, kind: "token" | "url") {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(kind);
      setTimeout(() => setCopied(""), 2000);
    });
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <UserPlus size={16} className="text-accent" />
        <h3 className="text-[13px] font-black text-text-primary">초대 생성</h3>
      </div>
      <button
        type="button"
        onClick={handleCreate}
        disabled={busy || !meetingId}
        className="w-full rounded-lg border border-accent/40 bg-accent/10 px-3 py-2 text-[12px] font-bold text-accent transition hover:bg-accent/20 disabled:opacity-50"
      >
        {busy ? "생성 중..." : "초대 링크 생성"}
      </button>
      {error && (
        <p className="rounded border border-danger/30 bg-danger/10 px-2 py-1.5 text-[11px] text-danger">
          {error}
        </p>
      )}
      {invite && (
        <div className="space-y-2 rounded-lg border border-online/30 bg-online/5 p-3">
          <div className="flex items-center justify-between gap-2">
            <span className="text-[11px] font-bold text-online">초대 생성됨</span>
            <span className="text-[10px] text-text-muted">{invite.agent_id}</span>
          </div>
          {invite.join_url && (
            <div className="space-y-1">
              <span className="text-[10px] font-bold text-text-muted">공개 링크</span>
              <div className="flex items-center gap-2">
                <code className="min-w-0 flex-1 truncate rounded bg-black/30 px-2 py-1.5 font-mono text-[10px] text-text-secondary">
                  {invite.join_url}
                </code>
                <button
                  type="button"
                  onClick={() => handleCopy(invite.join_url!, "url")}
                  className="shrink-0 rounded border border-line/60 bg-panel/50 p-1.5 text-text-muted transition hover:text-accent"
                  title="링크 복사"
                >
                  <Link size={13} />
                </button>
              </div>
              {copied === "url" && (
                <p className="text-[10px] font-bold text-online">링크 복사됨</p>
              )}
            </div>
          )}
          <div className="space-y-1">
            <span className="text-[10px] font-bold text-text-muted">토큰</span>
            <div className="flex items-center gap-2">
              <code className="min-w-0 flex-1 truncate rounded bg-black/30 px-2 py-1.5 font-mono text-[10px] text-text-secondary">
                {invite.invite_token}
              </code>
              <button
                type="button"
                onClick={() => handleCopy(invite.invite_token, "token")}
                className="shrink-0 rounded border border-line/60 bg-panel/50 p-1.5 text-text-muted transition hover:text-accent"
                title="토큰 복사"
              >
                <Copy size={13} />
              </button>
            </div>
            {copied === "token" && (
              <p className="text-[10px] font-bold text-online">토큰 복사됨</p>
            )}
          </div>
          <p className="text-[10px] text-text-muted">
            {invite.join_url
              ? "이 링크를 친구에게 전달하세요. 일회용이며 만료됩니다."
              : "공개 주소를 먼저 준비하면 친구에게 바로 보낼 링크가 생성됩니다."}
          </p>
        </div>
      )}
    </div>
  );
}

// --- Host: Pending Invites List ---

function PendingInvitesList() {
  const [invites, setInvites] = useState<PendingInvite[]>([]);

  useEffect(() => {
    fetchPendingInvites()
      .then((data) => setInvites(data.invites || []))
      .catch(() => {});
    const interval = setInterval(() => {
      fetchPendingInvites()
        .then((data) => setInvites(data.invites || []))
        .catch(() => {});
    }, 5000);
    return () => clearInterval(interval);
  }, []);

  async function handleRevoke(inviteId: string) {
    try {
      await revokeRoomInvite(inviteId);
      setInvites((prev) => prev.map((i) => i.invite_id === inviteId ? { ...i, revoked: true } : i));
    } catch {}
  }

  const active = invites.filter((i) => !i.revoked);
  if (!active.length) return null;

  return (
    <div className="space-y-2">
      <span className="text-[11px] font-bold text-text-muted">대기 중인 초대</span>
      {active.map((inv) => (
        <div key={inv.invite_id} className="flex items-center justify-between rounded border border-line/40 bg-black/10 px-2 py-1.5">
          <div className="min-w-0">
            <span className="text-[11px] font-bold text-text-primary">{inv.display_name}</span>
            <span className="ml-2 text-[10px] text-text-muted">{inv.agent_id}</span>
          </div>
          <button
            type="button"
            onClick={() => handleRevoke(inv.invite_id)}
            className="shrink-0 rounded p-1 text-text-muted transition hover:text-danger"
            title="취소"
          >
            <X size={12} />
          </button>
        </div>
      ))}
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

  // Parse token from URL if present
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const urlToken = params.get("token");
    if (urlToken) setToken(urlToken);
  }, []);

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

export default function RoomInvitePanel({
  meetingId,
  guestOnly = false,
}: {
  meetingId: string;
  guestOnly?: boolean;
}) {
  const [mode, setMode] = useState<"host" | "guest">(() => (guestOnly ? "guest" : "host"));
  const [session, setSession] = useState<{
    token: string;
    agentId: string;
    displayName: string;
    meetingId: string;
  } | null>(null);

  // Auto-switch to guest mode if URL has ?token=
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (guestOnly || params.get("token")) setMode("guest");
  }, [guestOnly]);

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
      {!guestOnly && (
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
      )}
      {mode === "host" ? (
        <HostTokenGate>
          <PublicInviteSetup />
          <InviteCreator meetingId={meetingId} />
          <PendingInvitesList />
        </HostTokenGate>
      ) : (
        <GuestJoinForm onJoined={handleJoined} />
      )}
    </div>
  );
}
