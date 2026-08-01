import { useEffect, useState } from "react";
import { Save } from "lucide-react";
import type { RoomAgentSession } from "../../api";
import type { NativeCliProviderAvailability } from "../../roomSocketClient";
import { providerCatalogGroup } from "../../lib/providerCatalogGroups";
import AgentPersonaPicker from "./AgentPersonaPicker";

export default function AgentSessionPersonaSettings({
  session,
  provider,
  canConfigure,
  onConfigure,
  onStatus,
}: {
  session: RoomAgentSession;
  provider: NativeCliProviderAvailability;
  canConfigure: boolean;
  onConfigure: (session: RoomAgentSession, settings: Record<string, string>) => void | Promise<void>;
  onStatus: (status: string) => void;
}) {
  const [personaCardId, setPersonaCardId] = useState(session.persona_card_id || "");
  const [busy, setBusy] = useState(false);
  const supported = ["api", "local"].includes(providerCatalogGroup(provider));
  const dirty = personaCardId !== (session.persona_card_id || "");

  useEffect(() => {
    setPersonaCardId(session.persona_card_id || "");
  }, [session.session_id, session.persona_card_id]);

  if (!supported) return null;

  async function savePersona() {
    if (!canConfigure || !dirty || busy) return;
    setBusy(true);
    onStatus("");
    try {
      await onConfigure(session, { persona_card_id: personaCardId });
      onStatus(personaCardId ? "봇카드·모듈 교체 완료 · 다음 시작부터 적용" : "봇카드·모듈 적용 해제 완료");
    } catch (error) {
      onStatus(error instanceof Error ? error.message : "봇카드·모듈 저장 실패");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="dc-agent-persona-settings">
      <AgentPersonaPicker
        value={personaCardId}
        applied={session.persona_card}
        disabled={busy || !canConfigure}
        onChange={setPersonaCardId}
      />
      <div className="dc-agent-persona-actions">
        <p className="preserve-words">
          {session.persona_card
            ? `현재 적용 · ${session.persona_card.display_name}`
            : "현재 적용된 봇카드나 모듈이 없습니다."}
        </p>
        <button
          type="button"
          className="dc-member-session-button"
          disabled={!canConfigure || !dirty || busy}
          onClick={() => void savePersona()}
        >
          <Save size={14} />
          {!personaCardId && session.persona_card_id
            ? "적용 해제"
            : session.persona_card_id
              ? "적용 교체"
              : "적용"}
        </button>
      </div>
      {!canConfigure && (
        <p className="dc-member-session-status preserve-words">
          세션을 중지하면 봇카드·모듈을 교체할 수 있습니다.
        </p>
      )}
    </div>
  );
}
