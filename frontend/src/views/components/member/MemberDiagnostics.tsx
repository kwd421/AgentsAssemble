import {
  agentMemberSignals,
  agentQuotaWindowSignals,
  agentTruthBadges,
  lastObservedSummary,
} from "../../../lib/agentLabels";
import ProviderTruthChips from "../ProviderTruthChips";
import {
  inlineQuotaChips,
  signalTone,
} from "./memberHelpers";
import type { MemberEntry } from "./memberTypes";

export default function MemberDiagnostics({
  entry,
  agent,
}: {
  entry: MemberEntry;
  agent: NonNullable<MemberEntry["agent"]>;
}) {
  const quotaWindows = entry.canViewQuota ? agentQuotaWindowSignals(agent) : [];
  const quotaFallback = entry.canViewQuota ? inlineQuotaChips(agent) : [];
  const signals = agentMemberSignals(agent).filter((signal) => !/^5h |^1w /.test(signal.label));
  const lastObserved = lastObservedSummary(agent);

  return (
    <>
      <section className="dc-member-detail-section" aria-label={`${entry.displayName} 사용량`}>
        <h3>사용량</h3>
        {!entry.canViewQuota ? (
          <p className="dc-member-detail-note preserve-words">
            사용량은 이 AI를 소유한 참가자에게만 표시됩니다.
          </p>
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
        ) : (
          <div className="dc-member-quota-fallback">
            {quotaFallback.map((chip) => (
              <span key={`${chip.label}-${chip.value}`} data-tone={chip.tone} title={chip.title}>
                <b>{chip.label}</b>
                {chip.value}
              </span>
            ))}
          </div>
        )}
      </section>
      <section className="dc-member-detail-section" aria-label={`${entry.displayName} 세션 상태`}>
        <h3>연결 상태</h3>
        <div className="dc-member-signal-row">
          {signals.map((signal) => (
            <span
              key={signal.label}
              className="dc-member-signal preserve-words"
              data-tone={signalTone(signal.tone)}
              title={signal.title || signal.label}
            >
              {signal.label}
            </span>
          ))}
        </div>
        <ProviderTruthChips badges={agentTruthBadges(agent)} compact limit={4} />
        {lastObserved && <p className="dc-member-detail-note preserve-words">{lastObserved}</p>}
      </section>
    </>
  );
}
