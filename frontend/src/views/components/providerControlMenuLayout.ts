import { useCallback, useEffect, useRef } from "react";

const MENU_VISIBLE_ROWS = 7;
const MENU_ROW_GAP = 2;
const MENU_CHROME = 10;

export type MenuPosition = {
  left: number;
  top: number;
  width: number;
};

export function menuHeightCap(rowHeight: number): number {
  return (
    MENU_VISIBLE_ROWS * rowHeight +
    (MENU_VISIBLE_ROWS - 1) * MENU_ROW_GAP +
    MENU_CHROME
  );
}

/** Keep the scroll cap aligned to the height of a real option row. */
export function useWholeRowMenu() {
  const observerRef = useRef<ResizeObserver | null>(null);

  useEffect(() => () => observerRef.current?.disconnect(), []);

  return useCallback((node: HTMLElement | null) => {
    observerRef.current?.disconnect();
    if (!node) {
      observerRef.current = null;
      return;
    }
    const measure = () => {
      const row = node.querySelector("button");
      const height = row?.getBoundingClientRect().height ?? 0;
      if (height > 0) node.style.setProperty("--dc-select-row", `${height}px`);
    };
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    const row = node.querySelector("button");
    if (row) observer.observe(row);
    observerRef.current = observer;
  }, []);
}
