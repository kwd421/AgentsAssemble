import { useState } from "react";
import { Brain } from "lucide-react";

import type { RoomAgentSession } from "../../api";

export default function AgentActivitySettings({
  session,
  activityVisible,
  onActivityVisibilityChange,
  onConfigure,
  onStatus,
}: {
  session: RoomAgentSession;
  activityVisible: boolean;
  onActivityVisibilityChange?: (session: RoomAgentSession, visible: boolean) => void;
  onConfigure?: (
    session: RoomAgentSession,
    settings: Record<string, string>
  ) => void | Promise<void>;
  onStatus: (status: string) => void;
}) {
  const [sharingBusy, setSharingBusy] = useState(false);

  async function toggleSharedActivity() {
    if (!onConfigure || sharingBusy) return;
    setSharingBusy(true);
    onStatus("");
    try {
      await onConfigure(session, {
        share_activity: String(!session.share_activity),
      });
      onStatus(
        session.share_activity
          ? "생각과 작업을 소유자에게만 표시합니다."
          : "생각과 작업을 방 참가자에게 공개합니다."
      );
    } catch (error) {
      onStatus(error instanceof Error ? error.message : "공개 설정 저장 실패");
    } finally {
      setSharingBusy(false);
    }
  }

  return (
    <>
      <ActivitySwitch
        label="생각과 작업 표시"
        description="공개용 생각 요약과 안전하게 정리된 도구 활동만 표시합니다."
        valueLabel={activityVisible ? "켜짐" : "꺼짐"}
        enabled={activityVisible}
        disabled={!onActivityVisibilityChange}
        onToggle={() => onActivityVisibilityChange?.(session, !activityVisible)}
      />
      <ActivitySwitch
        label="다른 참가자에게 사고·작업 공개"
        description="끄면 이 에이전트의 소유자에게만 전달됩니다."
        valueLabel={session.share_activity ? "공개" : "비공개"}
        enabled={Boolean(session.share_activity)}
        disabled={!onConfigure || sharingBusy}
        onToggle={() => void toggleSharedActivity()}
      />
    </>
  );
}

function ActivitySwitch({
  label,
  description,
  valueLabel,
  enabled,
  disabled,
  onToggle,
}: {
  label: string;
  description: string;
  valueLabel: string;
  enabled: boolean;
  disabled: boolean;
  onToggle: () => void;
}) {
  return (
    <div className="dc-agent-activity-setting">
      <div className="dc-agent-activity-copy">
        <div>
          <Brain size={15} aria-hidden />
          <span>{label}</span>
        </div>
        <p>{description}</p>
      </div>
      <button
        type="button"
        className="dc-agent-activity-toggle"
        role="switch"
        aria-label={label}
        aria-checked={enabled}
        data-on={enabled}
        disabled={disabled}
        onClick={onToggle}
      >
        <span className="dc-agent-activity-switch" aria-hidden="true">
          <i />
        </span>
        <span>{valueLabel}</span>
      </button>
    </div>
  );
}
