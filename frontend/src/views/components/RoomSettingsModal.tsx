import { useEffect, useRef, useState, type ChangeEvent } from "react";
import { Image as ImageIcon, Trash2, UserPlus, X } from "lucide-react";
import {
  type ChannelNotificationSetting,
  type ChannelSettings,
  type ConversationMode,
  type RoomToolMode,
  uploadLobbyAttachment,
} from "../../api";
import {
  roomAppearanceStyle,
  type RoomAppearance,
} from "../../lib/roomAppearance";
import type { RoomDockItem } from "../../lib/roomDockModel";

const ROOM_CHANNEL_OPTIONS = [
  { id: "lobby", label: "general" },
];

const CHANNEL_NOTIFICATION_LABELS: Array<{
  value: ChannelNotificationSetting;
  label: string;
}> = [
  { value: "default", label: "서버 기본값" },
  { value: "all", label: "모든 메시지" },
  { value: "mentions", label: "@멘션만" },
  { value: "mute", label: "알림 끔" },
];

type RoomSettingsSectionId =
  | "settings-overview"
  | "settings-appearance"
  | "settings-channels"
  | "settings-notify"
  | "settings-invite"
  | "settings-delete";

export default function RoomSettingsModal({
  room,
  initialSectionId,
  appearance,
  channelSettings,
  settingsStatus,
  settingsError,
  conversationMode,
  toolMode,
  orderedExcludePreviousSpeaker,
  maxRelayTurns,
  canInvite,
  onClose,
  onInvite,
  onRoomChange,
  onAppearanceChange,
  onChannelSettingChange,
  onConversationModeChange,
  onToolModeChange,
  onOrderedExcludePreviousSpeakerChange,
  onMaxRelayTurnsChange,
  onRetrySettings,
  onDeleteRoom,
}: {
  room: RoomDockItem;
  initialSectionId?: RoomSettingsSectionId;
  appearance: RoomAppearance;
  channelSettings: Record<string, ChannelSettings>;
  settingsStatus: "loading" | "ready" | "saving" | "stale" | "error";
  settingsError: string;
  conversationMode: ConversationMode | null;
  toolMode: RoomToolMode | null;
  orderedExcludePreviousSpeaker: boolean | null;
  maxRelayTurns: number | null;
  canInvite: boolean;
  onClose: () => void;
  onInvite: () => void;
  onRoomChange: (updates: Partial<Pick<RoomDockItem, "label" | "topic" | "shortLabel">>) => void;
  onAppearanceChange: (updates: Partial<RoomAppearance>) => Promise<void>;
  onChannelSettingChange: (channelId: string, updates: Partial<ChannelSettings>) => void;
  onConversationModeChange: (mode: ConversationMode) => void;
  onToolModeChange: (mode: RoomToolMode) => void;
  onOrderedExcludePreviousSpeakerChange: (exclude: boolean) => void;
  onMaxRelayTurnsChange: (turns: number) => void;
  onRetrySettings: () => void;
  onDeleteRoom: (confirmationName: string) => Promise<void>;
}) {
  const [uploadStatus, setUploadStatus] = useState("");
  const [deleteConfirmation, setDeleteConfirmation] = useState("");
  const [deleteError, setDeleteError] = useState("");
  const [deleting, setDeleting] = useState(false);
  const bodyRef = useRef<HTMLDivElement | null>(null);
  const routingSettingsReady = settingsStatus === "ready";
  const routingSettingsMessage =
    settingsStatus === "loading"
      ? "서버 대화 설정을 불러오는 중입니다."
      : settingsStatus === "saving"
        ? "서버 대화 설정을 저장하는 중입니다."
        : settingsStatus === "stale"
          ? "서버 설정 동기화에 실패했습니다. 확인된 이전 값은 읽기 전용으로 표시됩니다."
          : settingsStatus === "error"
            ? "서버 대화 설정을 확인할 수 없어 변경할 수 없습니다."
            : "";

  useEffect(() => {
    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [onClose]);

  useEffect(() => {
    if (!initialSectionId) return;
    const body = bodyRef.current;
    const target = body?.querySelector<HTMLElement>(`#${initialSectionId}`);
    if (!body || !target) return;
    body.scrollTop = Math.max(0, target.offsetTop - body.offsetTop);
  }, [initialSectionId]);

  async function handleBannerFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.currentTarget.files?.[0];
    event.currentTarget.value = "";
    if (!file) return;
    setUploadStatus("배너 업로드 중...");
    try {
      const attachment = await uploadLobbyAttachment(file, {
        roomId: room.meetingId,
        purpose: "room_appearance",
      });
      await onAppearanceChange({
        bannerImage: attachment.url,
        bannerPreset: "custom",
      });
      setUploadStatus("배너 이미지 저장됨");
    } catch (error) {
      setUploadStatus(error instanceof Error ? error.message : "배너 업로드 실패");
    }
  }

  async function handleIconFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.currentTarget.files?.[0];
    event.currentTarget.value = "";
    if (!file) return;
    setUploadStatus("아이콘 업로드 중...");
    try {
      const attachment = await uploadLobbyAttachment(file, {
        roomId: room.meetingId,
        purpose: "room_appearance",
      });
      await onAppearanceChange({ iconImage: attachment.url });
      setUploadStatus("채팅방 아이콘 저장됨");
    } catch (error) {
      setUploadStatus(error instanceof Error ? error.message : "아이콘 업로드 실패");
    }
  }

  return (
    <div className="dc-settings-backdrop" role="presentation" onClick={onClose}>
      <section
        className="dc-settings-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="room-settings-title"
        onClick={(event) => event.stopPropagation()}
      >
        <aside className="dc-settings-nav">
          <p className="dc-settings-nav-label preserve-words">{room.label}</p>
          <a href="#settings-overview">개요</a>
          <a href="#settings-appearance">외형</a>
          <a href="#settings-channels">채널</a>
          <a href="#settings-notify">알림</a>
          <a href="#settings-invite">초대</a>
          <a href="#settings-delete">서버 삭제</a>
        </aside>
        <div ref={bodyRef} className="dc-settings-body chat-scroll">
          <header className="dc-settings-titlebar">
            <div>
              <h2 id="room-settings-title">서버 설정</h2>
              <p className="preserve-words">방 이름, 배너, 초대 범위를 이 화면에서 바로 바꿉니다.</p>
            </div>
            <button type="button" className="dc-settings-close" onClick={onClose} aria-label="설정 닫기">
              <X size={18} />
              <span>ESC</span>
            </button>
          </header>

          <section id="settings-overview" className="dc-settings-section">
            <h3>개요</h3>
            <label>
              서버 이름
              <input
                className="ops-input"
                value={room.label}
                onChange={(event) => {
                  const label = event.target.value.slice(0, 80);
                  onRoomChange({
                    label,
                    shortLabel: (appearance.iconLabel || label || room.meetingId)
                      .slice(0, 1)
                      .toUpperCase(),
                  });
                }}
              />
            </label>
            <label>
              방 주제
              <input
                className="ops-input"
                value={room.topic}
                onChange={(event) => onRoomChange({ topic: event.target.value.slice(0, 160) })}
              />
            </label>
            <div className="dc-settings-field">
              <p className="dc-settings-field-label">대화 모드</p>
              {routingSettingsMessage && (
                <div
                  className="mb-3 flex items-center justify-between gap-3 text-[13px] text-text-muted"
                  role={settingsStatus === "error" || settingsStatus === "stale" ? "alert" : "status"}
                  title={settingsError || undefined}
                >
                  <span className="preserve-words">{routingSettingsMessage}</span>
                  {(settingsStatus === "error" || settingsStatus === "stale") && (
                    <button type="button" className="dc-upload-button shrink-0" onClick={onRetrySettings}>
                      다시 불러오기
                    </button>
                  )}
                </div>
              )}
              <div className="dc-radio-stack">
                <label>
                  <input
                    type="radio"
                    name="conversation-mode"
                    checked={conversationMode === "ordered"}
                    disabled={!routingSettingsReady}
                    onChange={() => onConversationModeChange("ordered")}
                  />
                  <span className="preserve-words">
                    🔢 순서 — 새 메시지마다 후보 둘을 무작위로 비교해 덜 말한 에이전트 한 명만 방을 확인합니다. @멘션은 그 에이전트에게 다음 순서를 줍니다.
                  </span>
                </label>
                <label>
                  <input
                    type="radio"
                    name="conversation-mode"
                    checked={conversationMode === "ambient"}
                    disabled={!routingSettingsReady}
                    onChange={() => onConversationModeChange("ambient")}
                  />
                  <span className="preserve-words">
                    자유 토론 (실험적) — 새 메시지가 생기면 연결된 에이전트들이 방을 확인하고, 각자 말할지 정합니다.
                  </span>
                </label>
                {conversationMode === "continuous" && (
                  <label>
                    <input
                      type="radio"
                      name="conversation-mode"
                      checked
                      disabled={!routingSettingsReady}
                      onChange={() => onConversationModeChange("continuous")}
                    />
                    <span className="preserve-words">
                      기존 연쇄 대화 — 이전 방과의 호환을 위해 유지되는 레거시 모드입니다.
                    </span>
                  </label>
                )}
              </div>
              {conversationMode === "ordered" && (
                <label className="mt-3 flex items-start gap-3">
                  <input
                    type="checkbox"
                    checked={orderedExcludePreviousSpeaker === true}
                    disabled={!routingSettingsReady}
                    onChange={(event) =>
                      onOrderedExcludePreviousSpeakerChange(event.target.checked)
                    }
                  />
                  <span className="preserve-words">
                    직전 발언자 연속 선택 방지 — 다른 선택 가능한 에이전트가 있으면 직전 발언자를 다음 일반 선택 후보에서 제외합니다. @멘션은 이 제한보다 우선합니다.
                  </span>
                </label>
              )}
              {conversationMode === "continuous" && (
                <label>
                  최대 연쇄 발언 수
                  <input
                    className="ops-input"
                    type="number"
                    min={2}
                    max={20}
                    value={maxRelayTurns ?? ""}
                    disabled={!routingSettingsReady}
                    onChange={(event) =>
                      onMaxRelayTurnsChange(Math.min(20, Math.max(2, Number(event.target.value) || 2)))
                    }
                  />
                </label>
              )}
            </div>
            <div className="dc-settings-field">
              <p className="dc-settings-field-label">방 도구</p>
              <div className="dc-radio-stack">
                <label>
                  <input
                    type="radio"
                    name="room-tool-mode"
                    checked={toolMode === "chat"}
                    disabled={!routingSettingsReady}
                    onChange={() => onToolModeChange("chat")}
                  />
                  <span className="preserve-words">
                    일반 대화 — 방 읽기와 발언만 제공합니다.
                  </span>
                </label>
                <label>
                  <input
                    type="radio"
                    name="room-tool-mode"
                    checked={toolMode === "tabletop"}
                    disabled={!routingSettingsReady}
                    onChange={() => onToolModeChange("tabletop")}
                  />
                  <span className="preserve-words">
                    테이블탑 · D&amp;D — 검증된 서버 주사위와 무작위 선택을 추가합니다.
                  </span>
                </label>
              </div>
            </div>
          </section>

          <section id="settings-appearance" className="dc-settings-section">
            <h3>외형</h3>
            <div className="dc-settings-preview" style={roomAppearanceStyle(appearance)}>
              <span className="dc-settings-preview-icon" data-has-image={Boolean(appearance.iconImage)}>
                {appearance.iconImage ? "" : appearance.iconLabel || room.shortLabel}
              </span>
              <div>
                <p className="font-black preserve-words">{room.label}</p>
                <p className="text-[12px] text-text-muted preserve-words">{room.topic}</p>
              </div>
            </div>
            <div className="dc-preset-grid">
              {(["default", "forest", "midnight", "ember"] as RoomAppearance["bannerPreset"][]).map(
                (preset) => (
                  <button
                    key={preset}
                    type="button"
                    data-active={appearance.bannerPreset === preset}
                    data-preset={preset}
                    onClick={() => {
                      void onAppearanceChange({
                        bannerPreset: preset,
                        bannerImage: undefined,
                      }).catch(() => undefined);
                    }}
                  >
                    {preset === "default" ? "기본" : preset === "forest" ? "그린" : preset === "midnight" ? "미드나잇" : "엠버"}
                  </button>
                )
              )}
            </div>
            <div className="dc-upload-row">
              <label className="dc-upload-button">
                <ImageIcon size={15} />
                배너 이미지
                <input type="file" accept="image/*" onChange={handleBannerFile} />
              </label>
              <label className="dc-upload-button">
                <ImageIcon size={15} />
                채팅방 아이콘
                <input type="file" accept="image/*" onChange={handleIconFile} />
              </label>
              <label className="min-w-0 flex-1">
                아이콘 글자
                <input
                  className="ops-input"
                  value={appearance.iconLabel || room.shortLabel}
                  maxLength={2}
                  onChange={(event) => {
                    const iconLabel = event.target.value.slice(0, 2).toUpperCase();
                    void onAppearanceChange({ iconLabel }).catch(() => undefined);
                    onRoomChange({ shortLabel: iconLabel || room.shortLabel });
                  }}
                />
              </label>
            </div>
            {uploadStatus && <p className="dc-upload-status preserve-words">{uploadStatus}</p>}
          </section>

          <section id="settings-channels" className="dc-settings-section">
            <h3>채널 설정</h3>
            <div className="dc-channel-settings-list">
              {ROOM_CHANNEL_OPTIONS.map((channel) => {
                const setting = channelSettings[channel.id] || { notifications: "default" };
                return (
                  <label key={channel.id} className="dc-channel-settings-row">
                    <span>
                      <strong className="preserve-words">#{channel.label}</strong>
                      <small className="preserve-words">
                        {setting.lastReadAt ? `마지막 읽음 ${setting.lastReadAt}` : "읽음 기록 없음"}
                      </small>
                    </span>
                    <select
                      value={setting.notifications}
                      onChange={(event) =>
                        onChannelSettingChange(channel.id, {
                          notifications: event.target.value as ChannelNotificationSetting,
                        })
                      }
                    >
                      {CHANNEL_NOTIFICATION_LABELS.map((option) => (
                        <option key={option.value} value={option.value}>
                          {option.label}
                        </option>
                      ))}
                    </select>
                  </label>
                );
              })}
            </div>
          </section>

          <section id="settings-notify" className="dc-settings-section">
            <h3>알림</h3>
            <div className="dc-radio-stack">
              {[
                ["all", "모든 메시지"],
                ["mentions", "@멘션만"],
                ["mute", "알림 끔"],
              ].map(([value, label]) => (
                <label key={value}>
                  <input
                    type="radio"
                    name="room-notifications"
                    checked={appearance.notifications === value}
                    onChange={() =>
                      void onAppearanceChange({
                        notifications: value as RoomAppearance["notifications"],
                      }).catch(() => undefined)
                    }
                  />
                  {label}
                </label>
              ))}
            </div>
          </section>

          <section id="settings-invite" className="dc-settings-section">
            <h3>초대</h3>
            <div className="dc-radio-stack">
              <label>
                <input
                  type="radio"
                  name="invite-scope"
                  checked={appearance.inviteScope === "room"}
                  onChange={() => {
                    void onAppearanceChange({ inviteScope: "room" }).catch(
                      () => undefined
                    );
                  }}
                />
                초대 링크는 이 방만 표시
              </label>
              <label>
                <input
                  type="radio"
                  name="invite-scope"
                  checked={appearance.inviteScope === "read_only"}
                  onChange={() => {
                    void onAppearanceChange({ inviteScope: "read_only" }).catch(
                      () => undefined
                    );
                  }}
                />
                읽기 전용 초대처럼 표시
              </label>
            </div>
            {canInvite && (
              <button type="button" className="ops-cta dc-settings-invite" onClick={onInvite}>
                <UserPlus size={15} />
                초대 링크 만들기
              </button>
            )}
          </section>
          <section id="settings-delete" className="dc-settings-section">
            <h3>서버 삭제</h3>
            <p className="text-[13px] text-text-muted preserve-words">
              이 작업은 복구할 수 없습니다. 확인하려면 서버 이름{" "}
              <strong>“{room.label}”</strong>을 정확히 입력하세요.
            </p>
            <label>
              서버 이름
              <input
                className="ops-input"
                value={deleteConfirmation}
                onChange={(event) => {
                  setDeleteConfirmation(event.target.value);
                  setDeleteError("");
                }}
                autoComplete="off"
                placeholder={room.label}
              />
            </label>
            {deleteError && <p className="text-[13px] text-red-400 preserve-words">{deleteError}</p>}
            <button
              type="button"
              className="danger dc-upload-button"
              disabled={deleting || deleteConfirmation !== room.label}
              onClick={async () => {
                setDeleting(true);
                setDeleteError("");
                try {
                  await onDeleteRoom(deleteConfirmation);
                } catch (error) {
                  setDeleteError(error instanceof Error ? error.message : "서버 삭제에 실패했습니다.");
                } finally {
                  setDeleting(false);
                }
              }}
            >
              <Trash2 size={15} />
              {deleting ? "삭제 중..." : "서버 영구 삭제"}
            </button>
          </section>
        </div>
      </section>
    </div>
  );
}
