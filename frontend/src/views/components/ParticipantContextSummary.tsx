import type { LiveAgent } from "../../api";
import { roomContextSummaryBadges } from "../../lib/agentLabels";
import ProviderTruthChips from "./ProviderTruthChips";

export default function ParticipantContextSummary({ agents }: { agents: LiveAgent[] }) {
  const badges = roomContextSummaryBadges(agents);
  if (badges.length === 0) return null;

  return (
    <div aria-label="참가자 맥락 요약" className="mb-3 -mt-1">
      <ProviderTruthChips badges={badges} compact />
    </div>
  );
}
