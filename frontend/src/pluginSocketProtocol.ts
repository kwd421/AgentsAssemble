export interface PluginEnvelope {
  type: "plugin.snapshot" | "plugin.delta" | "plugin.error";
  plugin_id?: string;
  plugin_seq?: number;
  room_id?: string;
  payload?: Record<string, unknown>;
  message?: string;
  code?: string;
}

export class PluginStreamProtocolError extends Error {
  constructor(
    message: string,
    readonly code: "plugin_event_invalid" | "plugin_event_gap"
  ) {
    super(message);
  }
}

export function parsePluginEnvelopeBatch(
  rawEvents: unknown[],
  options: { currentSequence: number; advertisedLatestSequence: unknown }
): { events: PluginEnvelope[]; latestSequence: number } {
  const events: PluginEnvelope[] = [];
  let nextSequence = options.currentSequence;
  for (const rawEvent of rawEvents) {
    if (!isRecord(rawEvent)) throw invalidEnvelope();
    const event = rawEvent as unknown as PluginEnvelope;
    const eventType = String(event.type || "");
    if (!["plugin.snapshot", "plugin.delta", "plugin.error"].includes(eventType)) {
      throw invalidEnvelope();
    }
    const sequence = event.plugin_seq;
    if (eventType === "plugin.error" && (sequence === undefined || sequence === null)) {
      events.push(event);
      continue;
    }
    if (!isPositiveInteger(sequence)) throw invalidEnvelope();
    if (sequence <= nextSequence) continue;
    if (nextSequence > 0 && sequence !== nextSequence + 1) {
      throw new PluginStreamProtocolError(
        `Plugin event sequence gap detected (expected ${nextSequence + 1}, received ${sequence}); reconnecting.`,
        "plugin_event_gap"
      );
    }
    events.push(event);
    nextSequence = sequence;
  }
  const latestSequence = Number(options.advertisedLatestSequence ?? nextSequence);
  if (!Number.isInteger(latestSequence) || latestSequence < nextSequence) {
    throw invalidEnvelope();
  }
  return { events, latestSequence };
}

function invalidEnvelope(): PluginStreamProtocolError {
  return new PluginStreamProtocolError(
    "Plugin event did not match the plugin stream schema; reconnecting.",
    "plugin_event_invalid"
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function isPositiveInteger(value: unknown): value is number {
  return Number.isInteger(value) && Number(value) > 0;
}
