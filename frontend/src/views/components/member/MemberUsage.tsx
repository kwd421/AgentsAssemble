import { agentQuotaWindowSignals } from "../../../lib/agentLabels";
import {
  inlineQuotaChips,
  signalTone,
} from "./memberHelpers";
import type { MemberEntry } from "./memberTypes";

export default function MemberUsage({
  entry,
  agent,
}: {
  entry: MemberEntry;
  agent: NonNullable<MemberEntry["agent"]>;
}) {
  const quotaWindows = entry.canViewQuota ? agentQuotaWindowSignals(agent) : [];
  const quotaFallback = entry.canViewQuota ? inlineQuotaChips(agent) : [];

  return (
    <section className="dc-member-detail-section" aria-label={`${entry.displayName} 사용량`}>
      <h3>사용량</h3>
      {entry.canViewQuota && agent.quota_status === "stale" && (
        <p className="dc-member-detail-note preserve-words" data-tone="warning">
          새로 확인하지 못해 마지막으로 확인된 값을 표시합니다.
        </p>
      )}
      {!entry.canViewQuota ? (
        <p className="dc-member-detail-note preserve-words">
          사용량은 이 AI를 소유한 참가자에게만 표시됩니다.
        </p>
      ) : agent.quota_state === "exhausted" ? (
        <div className="dc-member-quota-status" data-tone="danger">
          <strong>할당량 소진</strong>
          <span>Provider가 더 이상 사용할 수 없다고 명시했습니다.</span>
        </div>
      ) : quotaWindows.length > 0 ? (
        <div className="dc-member-quota-row">
          {quotaWindows.map((window) => (
            <span
              key={`${window.label}-${window.percent}`}
              className="dc-member-quota-window"
              data-tone={signalTone(window.tone)}
              title={window.title}
              aria-label={window.title}
            >
              <span className="dc-member-quota-label preserve-words">{window.label}</span>
              <span className="dc-member-quota-bar" aria-hidden>
                <span style={{ width: `${window.percent}%` }} />
              </span>
              <span className="dc-member-quota-percent">{window.percent}%</span>
            </span>
          ))}
        </div>
      ) : quotaFallback.length > 0 ? (
        <div className="dc-member-quota-fallback">
          {quotaFallback.map((chip) => (
            <span key={`${chip.label}-${chip.value}`} data-tone={chip.tone} title={chip.title}>
              <b>{chip.label}</b>
              {chip.value}
            </span>
          ))}
        </div>
      ) : agent.quota_status === "loading" ? (
        <p className="dc-member-detail-note preserve-words">
          정확한 사용량을 확인하고 있습니다.
        </p>
      ) : agent.quota_status === "unavailable" ? (
        <p className="dc-member-detail-note preserve-words">
          Provider에서 정확한 사용량을 불러오지 못했습니다.
        </p>
      ) : (
        <p className="dc-member-detail-note preserve-words">
          이 Provider는 확인 가능한 정확한 잔여량을 제공하지 않습니다.
        </p>
      )}
    </section>
  );
}
