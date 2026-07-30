export interface RoomChannel {
  id: string;
  name: string;
  type: "text" | "voice";
  position: number;
  createdAt: string;
}

export type ApiRoomChannel = {
  id?: string;
  name?: string;
  type?: "text" | "voice";
  position?: number;
  created_at?: string;
};

export function normalizeRoomChannel(channel: ApiRoomChannel): RoomChannel {
  return {
    id: String(channel.id || ""),
    name: String(channel.name || ""),
    type: channel.type === "voice" ? "voice" : "text",
    position: Number(channel.position || 0),
    createdAt: String(channel.created_at || ""),
  };
}

export function normalizeRoomChannelList(channels: ApiRoomChannel[] | undefined): RoomChannel[] {
  return Array.isArray(channels) ? channels.map(normalizeRoomChannel) : [];
}

export function roomChannelListToApi(channels: RoomChannel[]): ApiRoomChannel[] {
  return channels.map((channel) => ({
    id: channel.id,
    name: channel.name,
    type: channel.type,
    position: channel.position,
    created_at: channel.createdAt,
  }));
}
