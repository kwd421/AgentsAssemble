import { useEffect, useMemo, useState, type MouseEvent } from "react";
import type { LucideIcon } from "lucide-react";
import {
  ChevronDown,
  Hash,
  LogOut,
  MailCheck,
  MessageCircle,
  Plus,
  Settings,
  UserPlus,
  Users,
  X,
} from "lucide-react";
import RoomInvitePanel from "./RoomInvitePanel";

export type DiscordRoomChannelId = "lobby" | "live" | "board" | "records";

export type DiscordRoomChannel = {
  id: DiscordRoomChannelId;
  label: string;
  icon: LucideIcon;
  description: string;
};

type ContextTarget = "server" | DiscordRoomChannelId;

type ContextMenuState = {
  x: number;
  y: number;
  target: ContextTarget;
};

type DiscordRoomSidebarProps = {
  channels: DiscordRoomChannel[];
  activeChannel: DiscordRoomChannelId | "home";
  adminOpen: boolean;
  meetingId: string;
  roomName: string;
  onlineCount: number;
  totalAgents: number;
  onSelectHome: () => void;
  onSelectChannel: (channel: DiscordRoomChannelId) => void;
  onSelectAdmin: () => void;
  onLeaveRoom: () => void;
};

function targetLabel(target: ContextTarget, channels: DiscordRoomChannel[]) {
  if (target === "server") return "서버";
  return channels.find((channel) => channel.id === target)?.label || "채널";
}

function clampMenuPosition(value: number, maxOffset: number) {
  if (typeof window === "undefined") return value;
  return Math.max(8, Math.min(value, maxOffset));
}

export default function DiscordRoomSidebar({
  channels,
  activeChannel,
  adminOpen,
  meetingId,
  roomName,
  onlineCount,
  totalAgents,
  onSelectHome,
  onSelectChannel,
  onSelectAdmin,
  onLeaveRoom,
}: DiscordRoomSidebarProps) {
  const [contextMenu, setContextMenu] = useState<ContextMenuState | null>(null);
  const [inviteOpen, setInviteOpen] = useState(false);
  const [lastReadAt, setLastReadAt] = useState("");
  const roomActive = !adminOpen && activeChannel !== "home";
  const unread = !lastReadAt;
  const roomInitial = useMemo(() => roomName.trim().slice(0, 1).toUpperCase() || "A", [roomName]);

  useEffect(() => {
    if (!contextMenu) return undefined;
    function closeMenu() {
      setContextMenu(null);
    }
    window.addEventListener("click", closeMenu);
    window.addEventListener("keydown", closeMenu);
    return () => {
      window.removeEventListener("click", closeMenu);
      window.removeEventListener("keydown", closeMenu);
    };
  }, [contextMenu]);

  function openContextMenu(event: MouseEvent, target: ContextTarget) {
    event.preventDefault();
    event.stopPropagation();
    setContextMenu({
      x: clampMenuPosition(event.clientX, window.innerWidth - 230),
      y: clampMenuPosition(event.clientY, window.innerHeight - 170),
      target,
    });
  }

  function markRead() {
    setLastReadAt(new Date().toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit" }));
    setContextMenu(null);
  }

  function openInvitePanel() {
    setInviteOpen(true);
    setContextMenu(null);
  }

  function leaveRoom() {
    setContextMenu(null);
    onLeaveRoom();
  }

  return (
    <aside className="relative hidden min-h-0 shrink-0 border-r border-[#1f2024] bg-[#1e1f22] text-[#dbdee1] md:flex">
      <nav
        className="flex w-[72px] shrink-0 flex-col items-center gap-2 overflow-y-auto bg-[#1e1f22] px-3 py-3 chat-scroll"
        aria-label="서버 목록"
      >
        <button
          type="button"
          onClick={onSelectHome}
          aria-label="Discord 홈"
          className={`relative grid h-12 w-12 place-items-center rounded-2xl text-white transition-all ${
            activeChannel === "home" && !adminOpen
              ? "rounded-2xl bg-[#5865f2]"
              : "rounded-[24px] bg-[#313338] hover:rounded-2xl hover:bg-[#5865f2]"
          }`}
        >
          {activeChannel === "home" && !adminOpen && (
            <span className="absolute -left-3 h-10 w-1 rounded-r bg-white" />
          )}
          <Users size={22} />
        </button>
        <span className="h-px w-8 bg-[#35363c]" aria-hidden />
        <button
          type="button"
          onClick={() => onSelectChannel("lobby")}
          onContextMenu={(event) => openContextMenu(event, "server")}
          aria-label={`${roomName} 서버`}
          className={`relative grid h-12 w-12 place-items-center rounded-2xl font-black text-white transition-all ${
            roomActive
              ? "rounded-2xl bg-[#5865f2]"
              : "rounded-[24px] bg-[#313338] hover:rounded-2xl hover:bg-[#5865f2]"
          }`}
        >
          {roomActive && <span className="absolute -left-3 h-10 w-1 rounded-r bg-white" />}
          {!roomActive && unread && (
            <span className="absolute -left-3 h-2 w-1 rounded-r bg-white" />
          )}
          <span>{roomInitial}</span>
        </button>
        <button
          type="button"
          disabled
          title="새 방 추가는 다음 단계"
          className="grid h-12 w-12 cursor-not-allowed place-items-center rounded-[24px] bg-[#313338] text-[#23a559] opacity-70"
        >
          <Plus size={22} />
        </button>
      </nav>

      <section className="flex w-[250px] shrink-0 flex-col bg-[#2b2d31]" aria-label="방 채널">
        {activeChannel === "home" && !adminOpen ? (
          <>
            <div className="border-b border-[#1f2024] px-3 py-3">
              <button
                type="button"
                className="w-full rounded-md bg-[#1e1f22] px-3 py-2 text-left text-[13px] font-bold text-[#b5bac1]"
              >
                대화 찾기 또는 시작하기
              </button>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto p-2 chat-scroll">
              <button
                type="button"
                onClick={onSelectHome}
                className="flex w-full items-center gap-3 rounded px-3 py-2 text-left text-[15px] font-bold text-white"
              >
                <Users size={18} />
                친구
              </button>
              <button
                type="button"
                disabled
                className="mt-1 flex w-full cursor-not-allowed items-center gap-3 rounded px-3 py-2 text-left text-[15px] font-bold text-[#949ba4]"
              >
                <MessageCircle size={18} />
                다이렉트 메시지
              </button>
            </div>
          </>
        ) : (
          <>
            <button
              type="button"
              onContextMenu={(event) => openContextMenu(event, "server")}
              className="flex h-12 shrink-0 items-center justify-between border-b border-[#1f2024] px-4 text-left text-[15px] font-black text-white shadow-sm transition hover:bg-white/[0.04]"
            >
              <span className="min-w-0 truncate preserve-words">{roomName}</span>
              <ChevronDown size={16} className="shrink-0 text-[#b5bac1]" />
            </button>

            <div className="min-h-0 flex-1 overflow-y-auto px-2 py-3 chat-scroll">
              <div className="mb-2 flex items-center justify-between px-2 text-[11px] font-black uppercase tracking-wide text-[#949ba4]">
                <span>Text Channels</span>
                {lastReadAt && (
                  <span className="normal-case tracking-normal">읽음 {lastReadAt}</span>
                )}
              </div>
              <div className="space-y-0.5">
                {channels.map(({ id, label, icon: Icon, description }) => {
                  const active = !adminOpen && activeChannel === id;
                  return (
                    <button
                      key={id}
                      type="button"
                      onClick={() => onSelectChannel(id)}
                      onContextMenu={(event) => openContextMenu(event, id)}
                      className={`group flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-[15px] font-bold transition-colors ${
                        active
                          ? "bg-[#404249] text-white"
                          : "text-[#949ba4] hover:bg-white/[0.04] hover:text-[#dbdee1]"
                      }`}
                      title={description}
                    >
                      <Hash size={19} className={active ? "text-[#dbdee1]" : "text-[#80848e]"} />
                      <span className="min-w-0 flex-1 truncate">{label}</span>
                      <Icon size={14} className="shrink-0 opacity-0 transition-opacity group-hover:opacity-70" />
                    </button>
                  );
                })}
              </div>
            </div>

            <div className="shrink-0 border-t border-[#1f2024] p-2">
              <button
                type="button"
                onClick={onSelectAdmin}
                className={`flex w-full items-center gap-2 rounded px-2 py-2 text-left text-[13px] font-bold transition-colors ${
                  adminOpen ? "bg-[#404249] text-white" : "text-[#b5bac1] hover:bg-white/[0.04] hover:text-white"
                }`}
              >
                <Settings size={16} />
                방 설정
              </button>
              <div className="mt-2 rounded bg-[#232428] px-2 py-2 text-[11px] text-[#949ba4]">
                <span className="font-bold text-[#dbdee1]">{onlineCount}</span>
                <span> / {totalAgents || 0} online</span>
              </div>
            </div>
          </>
        )}

        {inviteOpen && (
          <div className="absolute left-[82px] top-[58px] z-30 w-[360px] max-w-[calc(100vw-120px)] rounded-xl border border-[#3f4147] bg-[#2b2d31] p-3 shadow-[0_18px_60px_rgba(0,0,0,0.55)]">
            <div className="mb-3 flex items-center justify-between gap-3">
              <div>
                <p className="text-[14px] font-black text-white">서버에 초대하기</p>
                <p className="text-[11px] text-[#949ba4] preserve-words">{roomName}</p>
              </div>
              <button
                type="button"
                onClick={() => setInviteOpen(false)}
                className="grid h-8 w-8 place-items-center rounded text-[#b5bac1] hover:bg-white/[0.06] hover:text-white"
                aria-label="초대 패널 닫기"
              >
                <X size={16} />
              </button>
            </div>
            <RoomInvitePanel meetingId={meetingId} />
          </div>
        )}
      </section>

      {contextMenu && (
        <div
          className="fixed z-40 w-[220px] rounded-md border border-black/40 bg-[#111214] p-1 text-[13px] font-bold text-[#dbdee1] shadow-[0_16px_44px_rgba(0,0,0,0.6)]"
          style={{ left: contextMenu.x, top: contextMenu.y }}
          role="menu"
          aria-label={`${targetLabel(contextMenu.target, channels)} 메뉴`}
          onClick={(event) => event.stopPropagation()}
        >
          <button
            type="button"
            onClick={markRead}
            className="flex w-full items-center gap-2 rounded px-3 py-2 text-left hover:bg-[#5865f2] hover:text-white"
            role="menuitem"
          >
            <MailCheck size={15} />
            읽음으로 표시하기
          </button>
          <button
            type="button"
            onClick={openInvitePanel}
            className="flex w-full items-center gap-2 rounded px-3 py-2 text-left hover:bg-[#5865f2] hover:text-white"
            role="menuitem"
          >
            <UserPlus size={15} />
            서버에 초대하기
          </button>
          <span className="my-1 block h-px bg-[#2b2d31]" aria-hidden />
          <button
            type="button"
            onClick={leaveRoom}
            className="flex w-full items-center gap-2 rounded px-3 py-2 text-left text-[#f23f42] hover:bg-[#da373c] hover:text-white"
            role="menuitem"
          >
            <LogOut size={15} />
            서버 나가기
          </button>
        </div>
      )}
    </aside>
  );
}
