import type { ProviderUsageId, ProviderUsageSnapshot } from "../api";

export function providerUsageAfterFailure(
  previous: ProviderUsageSnapshot | undefined,
  providerId: ProviderUsageId
): ProviderUsageSnapshot {
  if (previous?.status === "ready" || previous?.status === "stale") {
    return {
      ...previous,
      status: "stale",
      error_code: "usage_unavailable",
    };
  }
  return {
    provider_id: providerId,
    status: "unavailable",
    source: "",
    observed_at: "",
    error_code: "usage_unavailable",
    quota_windows: [],
  };
}
