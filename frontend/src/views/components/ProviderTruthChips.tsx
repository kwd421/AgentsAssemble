import type { AgentTruthBadge } from "../../lib/agentLabels";

function toneClass(tone: AgentTruthBadge["tone"]): string {
  if (tone === "online") return "border-online/30 bg-online/10 text-online";
  if (tone === "idle") return "border-idle/35 bg-idle/10 text-idle";
  if (tone === "danger") return "border-danger/35 bg-danger/10 text-danger";
  if (tone === "muted") return "border-text-muted/25 bg-black/18 text-text-muted";
  return "border-accent/30 bg-accent/10 text-accent";
}

export default function ProviderTruthChips({
  badges,
  compact = false,
  limit,
}: {
  badges: AgentTruthBadge[];
  compact?: boolean;
  limit?: number;
}) {
  const visible = typeof limit === "number" ? badges.slice(0, limit) : badges;
  if (visible.length === 0) return null;

  return (
    <div className="mt-2 flex flex-wrap gap-1.5">
      {visible.map((badge, index) => (
        <span
          key={`${badge.label}-${index}`}
          title={badge.title || badge.label}
          className={`rounded-md border font-black preserve-words ${toneClass(badge.tone)} ${
            compact ? "px-1.5 py-0.5 text-[9px]" : "px-2 py-1 text-[10px]"
          }`}
        >
          {badge.label}
        </span>
      ))}
    </div>
  );
}
