import { createContext, useContext, type ReactNode } from "react";
import type { RoomSocketHandle } from "./roomSocketClient";

const RoomSocketContext = createContext<RoomSocketHandle | null>(null);

export function RoomSocketProvider({
  socket,
  children,
}: {
  socket: RoomSocketHandle | null;
  children: ReactNode;
}) {
  return <RoomSocketContext.Provider value={socket}>{children}</RoomSocketContext.Provider>;
}

export function useRoomSocket(): RoomSocketHandle | null {
  return useContext(RoomSocketContext);
}
