export type RoomRailMenuPoint = {
  x: number;
  y: number;
};

export type RoomRailMenuViewport = {
  width: number;
  height: number;
};

export type RoomRailMenuSize = {
  width: number;
  height: number;
};

export type RoomRailMenuPosition = {
  left: number;
  top: number;
};

export const ROOM_RAIL_MENU_SIZE: RoomRailMenuSize = {
  width: 220,
  height: 220,
};

export const ROOM_RAIL_MENU_VIEWPORT_MARGIN = 8;

export function roomRailMenuPosition(
  point: RoomRailMenuPoint,
  viewport: RoomRailMenuViewport,
  menuSize = ROOM_RAIL_MENU_SIZE,
  margin = ROOM_RAIL_MENU_VIEWPORT_MARGIN
): RoomRailMenuPosition {
  const maxLeft = Math.max(margin, viewport.width - menuSize.width - margin);
  const maxTop = Math.max(margin, viewport.height - menuSize.height - margin);

  return {
    left: Math.min(Math.max(point.x, margin), maxLeft),
    top: Math.min(Math.max(point.y, margin), maxTop),
  };
}
