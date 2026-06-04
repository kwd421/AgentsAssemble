import type { SideChatEvent } from "../api";

export type SideChatThreadContext = {
  sourceEventId: string;
  sourceName: string;
  sourceMessage: string;
  channelLabel: string;
};

export type LobbyThreadSummary = {
  replyCount: number;
  lastReplyName: string;
  lastReplyAt: string;
};

function eventTimestampMs(value?: string): number {
  const time = Date.parse(value || "");
  return Number.isFinite(time) ? time : 0;
}

function threadSourceEventId(event: SideChatEvent): string {
  return String(event.thread_source_event_id || "").trim();
}

export function sideChatEventsForThreadContext(
  events: SideChatEvent[],
  threadContext: SideChatThreadContext | null
): SideChatEvent[] {
  if (!threadContext) {
    return events.filter((event) => !threadSourceEventId(event));
  }
  return events.filter((event) => threadSourceEventId(event) === threadContext.sourceEventId);
}

export function threadSummariesForSideChat(events: SideChatEvent[]): Record<string, LobbyThreadSummary> {
  const summaries: Record<string, LobbyThreadSummary> = {};
  events.forEach((event) => {
    const sourceEventId = threadSourceEventId(event);
    if (!sourceEventId) return;
    const previous = summaries[sourceEventId];
    const eventTime = eventTimestampMs(event.created_at);
    const previousTime = eventTimestampMs(previous?.lastReplyAt);
    summaries[sourceEventId] = {
      replyCount: (previous?.replyCount || 0) + 1,
      lastReplyName:
        !previous || eventTime >= previousTime
          ? event.name || "사이드"
          : previous.lastReplyName,
      lastReplyAt:
        !previous || eventTime >= previousTime
          ? event.created_at || previous?.lastReplyAt || ""
          : previous.lastReplyAt,
    };
  });
  return summaries;
}
