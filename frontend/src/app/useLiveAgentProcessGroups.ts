import { useCallback, useEffect, useState } from "react";
import {
  fetchLiveAgentProcesses,
  type LiveAgentProcessGroup,
  type LiveAgentProcessesResponse,
} from "../api";

const EMPTY_PROCESS_GROUPS: LiveAgentProcessGroup[] = [];

export function useLiveAgentProcessGroups({
  activeMeetingId,
  guestLocked,
}: {
  activeMeetingId: string;
  guestLocked: boolean;
}) {
  const [processData, setProcessData] = useState<LiveAgentProcessesResponse | null>(null);
  const [refreshRevision, setRefreshRevision] = useState(0);

  const refresh = useCallback(() => {
    setRefreshRevision((revision) => revision + 1);
  }, []);

  useEffect(() => {
    if (guestLocked) {
      setProcessData(null);
      return undefined;
    }
    let cancelled = false;
    fetchLiveAgentProcesses()
      .then((payload) => {
        if (!cancelled) setProcessData(payload);
      })
      .catch(() => {
        // Legacy process controls are optional for canonical Agent Sessions.
      });
    return () => {
      cancelled = true;
    };
  }, [activeMeetingId, guestLocked, refreshRevision]);

  return {
    processGroups: processData?.groups ?? EMPTY_PROCESS_GROUPS,
    refresh,
  };
}
