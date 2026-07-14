import { useEffect, useState } from "react";
import {
  updateLiveAgentSessionAgentOptions,
  uploadLobbyAttachment,
  type LiveAgentProcessGroup,
  type RoomAgentSession,
} from "../../../api";
import {
  removeAgentProfileSettings,
  saveAgentProfileSettings,
  type AgentProfileSettings,
} from "../../../lib/agentProfileSettings";
import {
  permissionOptionsForKind,
  providerAppliesOptionsLive,
  providerSupportsFast,
} from "../../../lib/liveAgentPermissionOptions";
import ImageCropper from "../ImageCropper";
import { sessionProcessGroupForAgent } from "./memberHelpers";
import type { MemberEntry } from "./memberTypes";

export default function AgentIdentitySettings({
  entry,
  agent,
  processGroups,
  onSessionActionComplete,
  onAgentProfileSettingsChange,
  onAgentConfigure,
}: {
  entry: MemberEntry;
  agent: NonNullable<MemberEntry["agent"]>;
  processGroups: LiveAgentProcessGroup[];
  onSessionActionComplete?: () => void;
  onAgentProfileSettingsChange?: (settings: Record<string, AgentProfileSettings>) => void;
  onAgentConfigure?: (
    session: RoomAgentSession,
    settings: Record<string, string>
  ) => void | Promise<void>;
}) {
  const [agentNameDraft, setAgentNameDraft] = useState(
    entry.agentProfile?.displayName || entry.agentDisplayName || ""
  );
  const [agentAvatarImage, setAgentAvatarImage] = useState(
    entry.agentProfile?.avatarImage || entry.avatarImage || ""
  );
  const [agentProfileCropFile, setAgentProfileCropFile] = useState<File | null>(null);
  const [agentProfileStatus, setAgentProfileStatus] = useState("");
  const [permissionDraft, setPermissionDraft] = useState(agent.permission_option || "");
  const [fastModeDraft, setFastModeDraft] = useState(Boolean(agent.fast_mode));
  const [optionsBusy, setOptionsBusy] = useState(false);
  const [optionsStatus, setOptionsStatus] = useState("");

  useEffect(() => {
    setAgentNameDraft(entry.agentProfile?.displayName || entry.agentDisplayName || "");
    setAgentAvatarImage(entry.agentProfile?.avatarImage || entry.avatarImage || "");
  }, [
    entry.agentDisplayName,
    entry.agentProfile?.avatarImage,
    entry.agentProfile?.displayName,
    entry.avatarImage,
  ]);

  useEffect(() => {
    setAgentProfileStatus("");
  }, [entry.agent?.agent_id]);

  const DetailIcon = entry.icon;
  const canEditAgentProfile = entry.ownedByViewer;
  const permissionOptions = permissionOptionsForKind(agent.provider_kind);
  const supportsFast = providerSupportsFast(agent.provider_kind);
  const sessionGroup = sessionProcessGroupForAgent(agent, processGroups);
  const optionsConfigPath = agent.live_agent_config_path || sessionGroup?.config_path || "";
  const canEditAgentOptions = Boolean(
    canEditAgentProfile && (permissionOptions.length > 0 || supportsFast)
  );
  const optionsDirty =
    permissionDraft !== (agent.permission_option || "") ||
    fastModeDraft !== Boolean(agent.fast_mode);

  async function handleAgentAvatarCropped(file: File) {
    setAgentProfileStatus("프로필 사진 저장 중...");
    try {
      const attachment = await uploadLobbyAttachment(file);
      setAgentAvatarImage(attachment.url);
      setAgentProfileCropFile(null);
      setAgentProfileStatus("프로필 사진 준비됨");
    } catch (error) {
      setAgentProfileStatus(error instanceof Error ? error.message : "프로필 사진 저장 실패");
    }
  }

  async function handleSaveAgentProfile() {
    setAgentProfileStatus("에이전트 프로필 저장 중...");
    try {
      let nextProfiles: Record<string, AgentProfileSettings>;
      if (entry.agentSession && onAgentConfigure) {
        await onAgentConfigure(entry.agentSession, {
          display_name: agentNameDraft,
          avatar_image_url: agentAvatarImage,
        });
        nextProfiles = removeAgentProfileSettings(agent.agent_id);
      } else {
        nextProfiles = saveAgentProfileSettings(agent.agent_id, {
          displayName: agentNameDraft,
          avatarImage: agentAvatarImage,
        });
      }
      onAgentProfileSettingsChange?.(nextProfiles);
      onSessionActionComplete?.();
      setAgentProfileStatus("에이전트 프로필 저장됨");
    } catch (error) {
      setAgentProfileStatus(error instanceof Error ? error.message : "에이전트 프로필 저장 실패");
    }
  }

  async function handleSaveAgentOptions() {
    if (!canEditAgentOptions || optionsBusy) return;
    setOptionsBusy(true);
    setOptionsStatus("권한/속도 저장 중...");
    try {
      const response = await updateLiveAgentSessionAgentOptions({
        agentId: agent.agent_id,
        liveAgentConfigPath: optionsConfigPath || undefined,
        ...(permissionOptions.length > 0 ? { permissionOption: permissionDraft } : {}),
        ...(supportsFast ? { fastMode: fastModeDraft } : {}),
      });
      setOptionsStatus(
        response.config_path
          ? "저장됨 · 다음 시작/재시작부터 적용"
          : "저장됨 (방 기록만) · 다음 시작부터 적용"
      );
      onSessionActionComplete?.();
    } catch (error) {
      setOptionsStatus(error instanceof Error ? error.message : "권한/속도 저장 실패");
    } finally {
      setOptionsBusy(false);
    }
  }

  return (
    <>
      {canEditAgentProfile && (
        <section className="dc-member-detail-section" aria-label={`${entry.displayName} 에이전트 프로필`}>
          <h3>에이전트 프로필</h3>
          <label className="dc-agent-profile-field">
            이름
            <input
              type="text"
              maxLength={80}
              value={agentNameDraft}
              onChange={(event) => setAgentNameDraft(event.currentTarget.value)}
              placeholder={agent.display_name || agent.agent_id}
            />
          </label>
          <div className="dc-agent-profile-avatar-row">
            <span className="dc-member-avatar dc-agent-profile-preview">
              {agentAvatarImage ? (
                <img className="dc-member-avatar-image" src={agentAvatarImage} alt="" />
              ) : (
                <DetailIcon size={18} />
              )}
            </span>
            <label className="dc-member-session-button">
              프로필 사진 편집
              <input
                className="sr-only"
                type="file"
                accept="image/*"
                onChange={(event) => {
                  const file = event.currentTarget.files?.[0] || null;
                  if (file) setAgentProfileCropFile(file);
                  event.currentTarget.value = "";
                }}
              />
            </label>
            <button type="button" className="dc-member-session-button" onClick={() => void handleSaveAgentProfile()}>
              저장
            </button>
          </div>
          {agentProfileCropFile && (
            <ImageCropper
              file={agentProfileCropFile}
              onCancel={() => setAgentProfileCropFile(null)}
              onCropped={(file) => void handleAgentAvatarCropped(file)}
            />
          )}
          {agentProfileStatus && (
            <p className="dc-member-session-status preserve-words">{agentProfileStatus}</p>
          )}
        </section>
      )}
      {canEditAgentOptions && (
        <section className="dc-member-detail-section" aria-label={`${entry.displayName} 권한·속도`}>
          <h3>권한 / 속도</h3>
          {permissionOptions.length > 0 && (
            <label className="dc-agent-profile-field">
              권한
              <select
                value={permissionDraft}
                onChange={(event) => setPermissionDraft(event.currentTarget.value)}
              >
                {permissionOptions.map((option) => (
                  <option key={option.id} value={option.id}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>
          )}
          {supportsFast && (
            <label className="dc-agent-start-toggle">
              <input
                type="checkbox"
                checked={fastModeDraft}
                onChange={(event) => setFastModeDraft(event.currentTarget.checked)}
              />
              <span>빠른 모드 (fast)</span>
            </label>
          )}
          <button
            type="button"
            className="dc-member-session-button"
            onClick={() => void handleSaveAgentOptions()}
            disabled={optionsBusy || !optionsDirty}
          >
            저장
          </button>
          <p className="dc-member-detail-note preserve-words">
            {providerAppliesOptionsLive(agent.provider_kind)
              ? "변경은 다음 턴부터 자동 적용됩니다. (재시작 불필요)"
              : "권한 변경은 재시작 후 적용됩니다. (claude는 세션 하나로 떠 있어 런치 플래그로 고정)"}
          </p>
          {optionsStatus && (
            <p className="dc-member-session-status preserve-words">{optionsStatus}</p>
          )}
        </section>
      )}
    </>
  );
}
