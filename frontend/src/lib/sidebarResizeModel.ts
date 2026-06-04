export const SIDEBAR_WIDTH_STORAGE_KEY = "agentsassemble.sidebar.width.v1";
export const SIDEBAR_WIDTH_DEFAULT = 312;
export const SIDEBAR_WIDTH_MIN = 220;
export const SIDEBAR_WIDTH_MAX = 420;

type SidebarWidthStorage = Pick<Storage, "getItem" | "setItem" | "removeItem">;

export function normalizeSidebarWidth(value: unknown): number {
  if (value === null || value === undefined || value === "") return SIDEBAR_WIDTH_DEFAULT;
  const width = Number(value);
  if (!Number.isFinite(width)) return SIDEBAR_WIDTH_DEFAULT;
  return Math.min(SIDEBAR_WIDTH_MAX, Math.max(SIDEBAR_WIDTH_MIN, Math.round(width)));
}

export function resizedSidebarWidth({
  startWidth,
  startX,
  currentX,
}: {
  startWidth: number;
  startX: number;
  currentX: number;
}): number {
  return normalizeSidebarWidth(startWidth + currentX - startX);
}

export function loadSidebarWidth(storage: SidebarWidthStorage = window.localStorage): number {
  try {
    return normalizeSidebarWidth(storage.getItem(SIDEBAR_WIDTH_STORAGE_KEY));
  } catch {
    return SIDEBAR_WIDTH_DEFAULT;
  }
}

export function persistSidebarWidth(
  width: unknown,
  storage: SidebarWidthStorage = window.localStorage
): void {
  try {
    storage.setItem(SIDEBAR_WIDTH_STORAGE_KEY, String(normalizeSidebarWidth(width)));
  } catch {
    // Best-effort UI preference; room state does not depend on local storage.
  }
}
