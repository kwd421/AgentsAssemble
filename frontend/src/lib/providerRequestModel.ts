import type {
  PublicProviderRequest,
  PublicProviderRequestOption,
  PublicProviderRequestQuestion,
  RoomEvent,
} from "../types/generatedRoomEvent";

export type ProviderRequestResolution =
  | { option_id: string }
  | { answers: Record<string, string[]> }
  | { acknowledged: true };

export type PendingProviderRequest = PublicProviderRequest & {
  provider_request_id: string;
  request_kind: string;
  response_kind: string;
  status: string;
  title: string;
  options: PublicProviderRequestOption[];
  questions: PublicProviderRequestQuestion[];
};

export function normalizePendingProviderRequests(
  requests: PublicProviderRequest[]
): PendingProviderRequest[] {
  return requests.flatMap((request) => {
    const normalized = normalizePendingProviderRequest(request);
    return normalized ? [normalized] : [];
  });
}

export function applyProviderRequestEvents(
  current: PendingProviderRequest[],
  events: RoomEvent[]
): PendingProviderRequest[] {
  const byId = new Map(current.map((request) => [request.provider_request_id, request]));
  events.forEach((event) => {
    const request = event.provider_request;
    const requestId = String(request?.provider_request_id || "").trim();
    if (!requestId) return;
    if (event.type === "provider_request_resolved") {
      byId.delete(requestId);
      return;
    }
    if (event.type === "provider_request_resolution_requested") {
      const existing = byId.get(requestId);
      if (existing) byId.set(requestId, { ...existing, status: "resolving" });
      return;
    }
    if (event.type !== "provider_request_opened") return;
    const normalized = normalizePendingProviderRequest({
      ...request,
      participant_id: request?.participant_id || event.participant_id,
      display_name: request?.display_name || event.display_name,
      provider_kind: request?.provider_kind || event.provider_kind,
    });
    if (normalized) byId.set(requestId, normalized);
  });
  return [...byId.values()];
}

function normalizePendingProviderRequest(
  request: PublicProviderRequest
): PendingProviderRequest | null {
  const providerRequestId = String(request.provider_request_id || "").trim();
  const responseKind = String(request.response_kind || "").trim();
  if (!providerRequestId || !["option", "answers", "acknowledge"].includes(responseKind)) {
    return null;
  }
  return {
    ...request,
    provider_request_id: providerRequestId,
    request_kind: String(request.request_kind || "permission"),
    response_kind: responseKind,
    status: String(request.status || "open"),
    title: String(request.title || "확인이 필요합니다"),
    options: request.options || [],
    questions: request.questions || [],
  };
}
