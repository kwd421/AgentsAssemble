import type { LobbyEvent } from "../../api";

export type LobbyRow =
  | { type: "divider"; key: string; label: string }
  | { type: "thinking"; key: string; events: LobbyEvent[]; showHeader: boolean }
  | { type: "event"; key: string; event: LobbyEvent; showHeader: boolean };

const GROUP_GAP_MS = 7 * 60 * 1000;

function dateKey(iso: string): string {
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return "";
  return `${parsed.getFullYear()}-${parsed.getMonth()}-${parsed.getDate()}`;
}

function dateDividerLabel(iso: string): string {
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return "";
  const today = new Date();
  const yesterday = new Date(today);
  yesterday.setDate(today.getDate() - 1);
  if (dateKey(iso) === dateKey(today.toISOString())) return "오늘";
  if (dateKey(iso) === dateKey(yesterday.toISOString())) return "어제";
  return parsed.toLocaleDateString("ko-KR", {
    year: "numeric",
    month: "long",
    day: "numeric",
    weekday: "long",
  });
}

export function buildLobbyRows(events: LobbyEvent[]): LobbyRow[] {
  const rows: LobbyRow[] = [];
  let lastDateKey = "";
  let previousAuthor = "";
  let previousTime = 0;
  let thinkingBuffer: LobbyEvent[] = [];
  const authorKey = (event: LobbyEvent) =>
    event.kind === "system" ||
    event.kind === "flow_event" ||
    event.kind === "vote_cast" ||
    event.kind === "vote_withdraw" ||
    event.kind === "vote_close"
      ? "::system"
      : event.actor_id || event.name || "";
  const timestamp = (iso: string) => Date.parse(iso || "") || 0;
  const flushThinking = () => {
    if (!thinkingBuffer.length) return;
    const key = authorKey(thinkingBuffer[0]);
    const startTime = timestamp(thinkingBuffer[0].created_at);
    const showHeader =
      key !== previousAuthor || startTime - previousTime > GROUP_GAP_MS;
    rows.push({
      type: "thinking",
      key: `think-${thinkingBuffer[0].id}`,
      events: thinkingBuffer,
      showHeader,
    });
    previousAuthor = key;
    previousTime =
      timestamp(thinkingBuffer[thinkingBuffer.length - 1].created_at) || startTime;
    thinkingBuffer = [];
  };

  for (const event of events) {
    const eventDateKey = dateKey(event.created_at);
    if (eventDateKey !== lastDateKey) {
      flushThinking();
      rows.push({
        type: "divider",
        key: `d-${event.id}`,
        label: dateDividerLabel(event.created_at),
      });
      lastDateKey = eventDateKey;
      previousAuthor = "";
      previousTime = 0;
    }
    if (event.kind === "thinking") {
      if (
        thinkingBuffer.length &&
        authorKey(thinkingBuffer[0]) !== authorKey(event)
      ) {
        flushThinking();
      }
      thinkingBuffer.push(event);
      continue;
    }
    flushThinking();
    const key = authorKey(event);
    const eventTime = timestamp(event.created_at);
    const showHeader =
      key !== previousAuthor || eventTime - previousTime > GROUP_GAP_MS;
    rows.push({ type: "event", key: event.id, event, showHeader });
    previousAuthor = key;
    previousTime = eventTime;
  }
  flushThinking();
  return rows;
}
