import { useState } from "react";
import { LogOut } from "lucide-react";
import type { RoomAgentSession } from "../../../api";
import type { NativeCliProviderAvailability } from "../../../roomSocketClient";
import AgentSessionDetails, {
  type AgentSessionControlAction,
} from "../AgentSessionDetails";
import type { MemberEntry } from "./memberTypes";

export default function AgentSessionMemberDetails({
  entry,
  session,
  onClose,
  onParticipantKick,
  onAgentControl,
  availableProviders,
  onAgentConfigure,
  activityVisible,
  onActivityVisibilityChange,
}: {
  entry: MemberEntry;
  session: RoomAgentSession;
  onClose: () => void;
  onParticipantKick?: (participantId: string) => void | Promise<void>;
  onAgentControl?: (
    session: RoomAgentSession,
    action: AgentSessionControlAction
  ) => void | Promise<void>;
  availableProviders: NativeCliProviderAvailability[];
  onAgentConfigure?: (
    session: RoomAgentSession,
    settings: Record<string, string>
  ) => void | Promise<void>;
  activityVisible?: boolean;
  onActivityVisibilityChange?: (session: RoomAgentSession, visible: boolean) => void;
}) {
  const [sessionActionBusy, setSessionActionBusy] = useState(false);
  const [sessionActionStatus, setSessionActionStatus] = useState("");

  async function kickParticipant() {
    if (!onParticipantKick || sessionActionBusy) return;
    if (!window.confirm(`${entry.displayName}을 이 방에서 추방할까요?`)) return;
    setSessionActionBusy(true);
    setSessionActionStatus("");
    try {
      await onParticipantKick(entry.id);
      onClose();
    } catch (error) {
      setSessionActionStatus(
        error instanceof Error ? error.message : "멤버를 추방하지 못했습니다"
      );
    } finally {
      setSessionActionBusy(false);
    }
  }

  return (
    <>
      <AgentSessionDetails
        session={session}
        provider={availableProviders.find((provider) => provider.provider_kind === session.provider_kind)}
        onControl={onAgentControl}
        onConfigure={onAgentConfigure}
        activityVisible={activityVisible}
        onActivityVisibilityChange={onActivityVisibilityChange}
      />
      {onParticipantKick && (
        <section className="dc-member-detail-section" aria-label={`${entry.displayName} 방 관리`}>
          <button
            type="button"
            className="dc-member-session-button"
            data-variant="danger"
            disabled={sessionActionBusy}
            onClick={() => void kickParticipant()}
          >
            <LogOut size={15} />
            추방
          </button>
          {sessionActionStatus && (
            <p className="dc-member-action-status preserve-words" role="alert">
              {sessionActionStatus}
            </p>
          )}
        </section>
      )}
    </>
  );
}
