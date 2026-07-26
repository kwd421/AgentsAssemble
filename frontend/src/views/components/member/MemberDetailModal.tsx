import { X } from "lucide-react";
import type { LiveAgentProcessGroup, RoomAgentSession } from "../../../api";
import type { AgentProfileSettings } from "../../../lib/agentProfileSettings";
import AgentSessionDetails, {
  type AgentSessionControlAction,
} from "../AgentSessionDetails";
import type { NativeCliProviderAvailability } from "../../../roomSocketClient";
import ProviderLogo from "../ProviderLogo";
import AgentIdentitySettings from "./AgentIdentitySettings";
import AgentSessionControls from "./AgentSessionControls";
import MemberDiagnostics from "./MemberDiagnostics";
import SessionOnlyMemberDetails from "./SessionOnlyMemberDetails";
import type { MemberEntry } from "./memberTypes";

export type MemberDetailModalProps = {
  entry: MemberEntry;
  onClose: () => void;
  processGroups?: LiveAgentProcessGroup[];
  onSessionActionComplete?: () => void;
  onAgentProfileSettingsChange?: (settings: Record<string, AgentProfileSettings>) => void;
  onParticipantKick?: (participantId: string) => void | Promise<void>;
  onAgentControl?: (
    session: RoomAgentSession,
    action: AgentSessionControlAction
  ) => void | Promise<void>;
  availableProviders?: NativeCliProviderAvailability[];
  onAgentConfigure?: (
    session: RoomAgentSession,
    settings: Record<string, string>
  ) => void | Promise<void>;
  activityVisible?: boolean;
  onActivityVisibilityChange?: (session: RoomAgentSession, visible: boolean) => void;
};

export default function MemberDetailModal({
  entry,
  onClose,
  processGroups = [],
  onSessionActionComplete,
  onAgentProfileSettingsChange,
  onParticipantKick,
  onAgentControl,
  availableProviders = [],
  onAgentConfigure,
  activityVisible,
  onActivityVisibilityChange,
}: MemberDetailModalProps) {
  if (!entry.agent && entry.agentSession) {
    return (
      <div className="dc-modal-backdrop" role="presentation" onClick={onClose}>
        <section
          className="dc-member-detail-modal"
          role="dialog"
          aria-modal="true"
          aria-labelledby="member-detail-title"
          onClick={(event) => event.stopPropagation()}
        >
          <header className="dc-member-detail-modal-head">
            <span className="dc-member-detail-modal-avatar" data-role={entry.role}>
              {entry.avatarImage ? (
                <img className="dc-member-avatar-image" src={entry.avatarImage} alt="" />
              ) : (
                <ProviderLogo providerKind={entry.providerKind} size={48} />
              )}
            </span>
            <div className="min-w-0 flex-1">
              <h2 id="member-detail-title" className="truncate preserve-words">
                {entry.displayName}
              </h2>
              <p className="truncate preserve-words">{entry.fullDetail || entry.detail}</p>
            </div>
            <button type="button" className="dc-modal-close" onClick={onClose} aria-label="멤버 정보 닫기">
              <X size={18} />
            </button>
          </header>
          <SessionOnlyMemberDetails
            entry={entry}
            session={entry.agentSession}
            onClose={onClose}
            onParticipantKick={onParticipantKick}
            onAgentControl={onAgentControl}
            availableProviders={availableProviders}
            onAgentConfigure={onAgentConfigure}
            activityVisible={activityVisible}
            onActivityVisibilityChange={onActivityVisibilityChange}
          />
        </section>
      </div>
    );
  }

  if (!entry.agent) return null;
  const agent = entry.agent;
  const DetailIcon = entry.icon;

  return (
    <div className="dc-modal-backdrop" role="presentation" onClick={onClose}>
      <section
        className="dc-member-detail-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="member-detail-title"
        onClick={(event) => event.stopPropagation()}
      >
        <header className="dc-member-detail-modal-head">
          <span className="dc-member-detail-modal-avatar" data-role={entry.role}>
            {entry.avatarImage ? (
              <img className="dc-member-avatar-image" src={entry.avatarImage} alt="" />
            ) : (
              <ProviderLogo
                providerKind={entry.providerKind}
                size={48}
                fallback={<DetailIcon size={22} />}
              />
            )}
          </span>
          <div className="min-w-0 flex-1">
            <h2 id="member-detail-title" className="truncate preserve-words">
              {entry.displayName}
            </h2>
            <p className="truncate preserve-words">{entry.fullDetail || entry.detail}</p>
          </div>
          <button type="button" className="dc-modal-close" onClick={onClose} aria-label="멤버 정보 닫기">
            <X size={18} />
          </button>
        </header>
        <MemberDiagnostics entry={entry} agent={agent} />
        {entry.agentSession && (
          <AgentSessionDetails
            session={entry.agentSession}
            provider={availableProviders.find(
              (provider) => provider.provider_kind === entry.agentSession?.provider_kind
            )}
            onControl={onAgentControl}
            onConfigure={onAgentConfigure}
            activityVisible={activityVisible}
            onActivityVisibilityChange={onActivityVisibilityChange}
          />
        )}
        <AgentIdentitySettings
          entry={entry}
          agent={agent}
          processGroups={processGroups}
          onSessionActionComplete={onSessionActionComplete}
          onAgentProfileSettingsChange={onAgentProfileSettingsChange}
          onAgentConfigure={onAgentConfigure}
        />
        <AgentSessionControls
          entry={entry}
          agent={agent}
          processGroups={processGroups}
          onSessionActionComplete={onSessionActionComplete}
          onParticipantKick={onParticipantKick}
          onClose={onClose}
        />
      </section>
    </div>
  );
}
