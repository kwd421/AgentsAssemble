import type { MouseEvent as ReactMouseEvent } from "react";
import { Check, LogOut, Plus, Settings, UserPlus, Users } from "lucide-react";
import {
  completeRoomAppearance,
  roomAppearanceStyle,
  type RoomAppearance,
} from "../../lib/roomAppearance";
import {
  roomSettingsKey,
  roomIsDisconnected,
  type RoomDockItem,
} from "../../lib/roomDockModel";
import {
  ROOM_RAIL_MENU_SIZE,
  ROOM_RAIL_MENU_VIEWPORT_MARGIN,
} from "../../lib/roomRailMenuPosition";
import "./RoomRail.css";

export type RoomMenuState = {
  roomId: string;
  x: number;
  y: number;
} | null;

export default function RoomRail({
  rooms,
  activeRoom,
  roomAppearances,
  guestLocked,
  adminOpen,
  channelIsFriends,
  menuRoom,
  roomMenu,
  onHomeClick,
  onSelectRoom,
  onAddRoom,
  onOpenRoomMenu,
  onMarkRoomRead,
  onInviteRoom,
  onOpenRoomSettings,
  onLeaveRoom,
}: {
  rooms: RoomDockItem[];
  activeRoom: RoomDockItem;
  roomAppearances: Record<string, RoomAppearance>;
  guestLocked: boolean;
  adminOpen: boolean;
  channelIsFriends: boolean;
  menuRoom?: RoomDockItem;
  roomMenu: RoomMenuState;
  onHomeClick: () => void;
  onSelectRoom: (roomId: string) => void;
  onAddRoom: () => void;
  onOpenRoomMenu: (event: ReactMouseEvent, room: RoomDockItem) => void;
  onMarkRoomRead: (roomId: string) => void;
  onInviteRoom: (roomId: string) => void;
  onOpenRoomSettings: (roomId: string) => void;
  onLeaveRoom: (roomId: string) => void;
}) {
  return (
    <nav
      className="dc-rail flex shrink-0 flex-col items-center gap-2 py-3"
      aria-label="룸 레일"
    >
      {!guestLocked && (
        <>
          <button
            type="button"
            onClick={onHomeClick}
            className="dc-rail-home"
            data-active={!adminOpen && channelIsFriends}
            aria-label="친구와 DM"
            title="친구"
          >
            <Users size={20} />
          </button>
          <span className="dc-server-divider" aria-hidden />
        </>
      )}
      <div className="dc-room-stack min-h-0 flex-1 overflow-y-auto chat-scroll" aria-label="방 목록">
        {rooms.map((room) => {
          const Icon = room.icon;
          const active = !adminOpen && !channelIsFriends && activeRoom.id === room.id;
          const disconnected = roomIsDisconnected(room);
          const roomAppearance = completeRoomAppearance(
            {
              ...room.appearance,
              ...(roomAppearances[roomSettingsKey(room)] || roomAppearances[room.id]),
            }
          );
          return (
            <button
              key={room.id}
              type="button"
              onClick={() => onSelectRoom(room.id)}
              onContextMenu={(event) => onOpenRoomMenu(event, room)}
              data-active={active}
              data-tone={room.tone}
              data-has-image={Boolean(roomAppearance.iconImage)}
              data-connection-state={disconnected ? "disconnected" : room.connectionState || "local"}
              style={roomAppearanceStyle(roomAppearance)}
              className="dc-server-btn"
              aria-label={`${room.label}${disconnected ? " · 연결이 끊긴 서버" : ""}`}
              title={`${room.label} · ${disconnected ? "연결이 끊긴 서버" : room.topic}`}
            >
              {roomAppearance.iconImage ? null : <Icon size={18} aria-hidden />}
              {disconnected && <span className="dc-server-connection-dot" aria-hidden />}
              <span className="sr-only">{room.shortLabel}</span>
            </button>
          );
        })}
        {!guestLocked && (
          <button
            type="button"
            onClick={onAddRoom}
            className="dc-server-btn dc-server-add"
            aria-label="새 방 만들기"
            title="새 방"
          >
            <Plus size={20} />
          </button>
        )}
      </div>
      {menuRoom && roomMenu && (
        <div
          className="dc-context-menu"
          style={{
            left: roomMenu.x,
            top: roomMenu.y,
            width: ROOM_RAIL_MENU_SIZE.width,
            maxHeight: `calc(100vh - ${roomMenu.y}px - ${ROOM_RAIL_MENU_VIEWPORT_MARGIN}px)`,
          }}
          role="menu"
          aria-label={`${menuRoom.label} 서버 메뉴`}
          onClick={(event) => event.stopPropagation()}
          onContextMenu={(event) => event.preventDefault()}
        >
          <p className="dc-context-title preserve-words">{menuRoom.label}</p>
          <button type="button" role="menuitem" onClick={() => onMarkRoomRead(menuRoom.id)}>
            <Check size={16} />
            읽음으로 표시하기
          </button>
          {!guestLocked && menuRoom && !roomIsDisconnected(menuRoom) && (
            <button type="button" role="menuitem" onClick={() => onInviteRoom(menuRoom.id)}>
              <UserPlus size={16} />
              서버에 초대하기
            </button>
          )}
          {!guestLocked && menuRoom && !roomIsDisconnected(menuRoom) && (
            <button type="button" role="menuitem" onClick={() => onOpenRoomSettings(menuRoom.id)}>
              <Settings size={16} />
              서버 설정
            </button>
          )}
          {guestLocked && (
            <>
              <span className="dc-context-separator" aria-hidden />
              <button type="button" role="menuitem" className="danger" onClick={() => onLeaveRoom(menuRoom.id)}>
                <LogOut size={16} />
                서버 나가기
              </button>
            </>
          )}
        </div>
      )}
      <div className="mt-auto" />
    </nav>
  );
}
