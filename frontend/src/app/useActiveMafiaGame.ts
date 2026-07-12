import { useCallback, useState } from "react";
import { fetchMafiaGame, type MafiaGame, type MafiaGameResponse } from "../api";
import { usePoll } from "../hooks";

const STORED_MAFIA_GAME_ID_KEY = "agentsassemble.mafiaGameId";

function loadStoredMafiaGameId(): string {
  try {
    return localStorage.getItem(STORED_MAFIA_GAME_ID_KEY) || "";
  } catch {
    return "";
  }
}

function saveStoredMafiaGameId(gameId: string) {
  try {
    localStorage.setItem(STORED_MAFIA_GAME_ID_KEY, gameId);
  } catch {
    // Browser storage can be unavailable in restricted contexts; in-memory state still works.
  }
}

function clearStoredMafiaGameId() {
  try {
    localStorage.removeItem(STORED_MAFIA_GAME_ID_KEY);
  } catch {
    // Clearing is best-effort when browser storage is restricted.
  }
}

function isMafiaGameMissingError(errorValue: unknown): boolean {
  const message = errorValue instanceof Error ? errorValue.message : String(errorValue || "");
  return message.includes("Mafia game was not found") || message.includes("404");
}

export type UseActiveMafiaGameOptions = {
  activeMeetingId: string;
};

export type UseActiveMafiaGameResult = {
  mafiaGame: MafiaGame | null;
  refreshMafia: () => void;
};

export function useActiveMafiaGame({
  activeMeetingId,
}: UseActiveMafiaGameOptions): UseActiveMafiaGameResult {
  const [mafiaGameId, setMafiaGameId] = useState(() => {
    try {
      const query = new URLSearchParams(window.location.search);
      const queryGameId = query.get("mafia") || query.get("mafiaGameId") || "";
      if (queryGameId) {
        saveStoredMafiaGameId(queryGameId);
        return queryGameId;
      }
      return loadStoredMafiaGameId();
    } catch {
      return "";
    }
  });
  const activeMafiaGameId = mafiaGameId === activeMeetingId ? mafiaGameId : "";
  const mafiaFetcher = useCallback((): Promise<MafiaGameResponse> => {
    if (!activeMafiaGameId) return Promise.resolve({ game: null });
    return fetchMafiaGame(activeMafiaGameId, "host").catch((errorValue) => {
      if (isMafiaGameMissingError(errorValue)) {
        clearStoredMafiaGameId();
        setMafiaGameId("");
        return { game: null };
      }
      throw errorValue;
    });
  }, [activeMafiaGameId]);
  const [mafiaData, , , refreshMafia] = usePoll<MafiaGameResponse>(mafiaFetcher, 3500);
  const mafiaGame = mafiaData?.game?.game_id === activeMeetingId ? mafiaData.game : null;

  return { mafiaGame, refreshMafia };
}
