import {
  lobbyEventId,
  type LobbyEvent,
} from "../api";

export type LiveTimelineSource = "flow" | "official";
export type LiveTimelineResetReason = "flow" | "meeting" | "source" | "";

function normalizedRef(value?: string): string {
  return String(value || "").trim();
}

function compareTimelineEvents(left: LobbyEvent, right: LobbyEvent): number {
  return String(left.created_at || "").localeCompare(String(right.created_at || ""));
}

function sameTimelineEvent(left: LobbyEvent, right: LobbyEvent): boolean {
  return JSON.stringify(left) === JSON.stringify(right);
}

function sameTimelineArray(left: LobbyEvent[], right: LobbyEvent[]): boolean {
  if (left.length !== right.length) return false;
  return left.every((event, index) => event === right[index]);
}

export function sortLiveTimelineEvents(events: LobbyEvent[]): LobbyEvent[] {
  return events.slice().sort(compareTimelineEvents);
}

export function liveTimelineResetReason({
  previousFlowId,
  nextFlowId,
  previousMeetingId,
  nextMeetingId,
  previousTimelineSource,
  nextTimelineSource,
}: {
  previousFlowId?: string;
  nextFlowId?: string;
  previousMeetingId?: string;
  nextMeetingId?: string;
  previousTimelineSource: LiveTimelineSource;
  nextTimelineSource: LiveTimelineSource;
}): LiveTimelineResetReason {
  if (normalizedRef(previousFlowId) !== normalizedRef(nextFlowId)) return "flow";
  if (previousTimelineSource !== nextTimelineSource) return "source";
  if (normalizedRef(previousMeetingId) !== normalizedRef(nextMeetingId)) return "meeting";
  return "";
}

export function mergeLiveTimelineEvents({
  previousEvents,
  incomingEvents,
  reset,
}: {
  previousEvents: LobbyEvent[];
  incomingEvents: LobbyEvent[];
  reset: boolean;
}): LobbyEvent[] {
  if (reset) return sortLiveTimelineEvents(incomingEvents);

  const byId = new Map<string, LobbyEvent>();
  const order: string[] = [];
  for (const event of previousEvents) {
    const eventId = lobbyEventId(event);
    if (!eventId) continue;
    byId.set(eventId, event);
    order.push(eventId);
  }
  for (const event of incomingEvents) {
    const eventId = lobbyEventId(event);
    if (!eventId) continue;
    if (!byId.has(eventId)) order.push(eventId);
    const previousEvent = byId.get(eventId);
    byId.set(eventId, previousEvent && sameTimelineEvent(previousEvent, event) ? previousEvent : event);
  }

  const merged = order
    .map((eventId) => byId.get(eventId))
    .filter(Boolean) as LobbyEvent[];
  const sorted = merged.sort(compareTimelineEvents);
  return sameTimelineArray(previousEvents, sorted) ? previousEvents : sorted;
}

export function nextTimelinePinnedToLatest(
  currentPinned: boolean,
  resetReason: LiveTimelineResetReason
): boolean {
  return resetReason ? true : currentPinned;
}

export function filterFlowTimelineEvents({
  incomingEvents,
  activeFlowId,
  activeMeetingId,
}: {
  incomingEvents: LobbyEvent[];
  activeFlowId?: string;
  activeMeetingId?: string;
}): LobbyEvent[] {
  const flowId = normalizedRef(activeFlowId);
  const meetingId = normalizedRef(activeMeetingId);
  return incomingEvents.filter((event) => {
    if (!event.id || (!event.flow_event_type && !event.flow_action)) return false;
    if (!flowId && meetingId) return event.flow_meeting_id === meetingId;
    if (!flowId) return true;
    if (event.flow_id !== flowId) return false;
    return !meetingId || !event.flow_meeting_id || event.flow_meeting_id === meetingId;
  });
}
