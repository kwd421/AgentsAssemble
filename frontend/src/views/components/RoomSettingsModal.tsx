import { useState, type ChangeEvent } from "react";
import { Image as ImageIcon, UserPlus, X } from "lucide-react";
import { type ChannelNotificationSetting, type ChannelSettings, uploadLobbyAttachment } from "../../api";
import type { RoomDockItem } from "../../App";
import {
  roomAppearanceStyle,
  type RoomAppearance,
} from "../../lib/roomAppearance";

const ROOM_CHANNEL_OPTIONS = [
  { id: "lobby", label: "general" },
  { id: "live", label: "live-room" },
  { id: "board", label: "work-board" },
  { id: "records", label: "records" },
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

export default function RoomSettingsModal({
  room,
  appearance,
  channelSettings,
  canInvite,
  onClose,
  onInvite,
  onRoomChange,
  onAppearanceChange,
  onChannelSettingChange,
}: {
  room: RoomDockItem;
  appearance: RoomAppearance;
  channelSettings: Record<string, ChannelSettings>;
  canInvite: boolean;
  onClose: () => void;
  onInvite: () => void;
  onRoomChange: (updates: Partial<Pick<RoomDockItem, "label" | "topic" | "shortLabel">>) => void;
  onAppearanceChange: (updates: Partial<RoomAppearance>) => void;
  onChannelSettingChange: (channelId: string, updates: Partial<ChannelSettings>) => void;
}) {
  const [uploadStatus, setUploadStatus] = useState("");

  async function handleBannerFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.currentTarget.files?.[0];
    event.currentTarget.value = "";
    if (!file) return;
    setUploadStatus("배너 업로드 중...");
    try {
      const attachment = await uploadLobbyAttachment(file);
      onAppearanceChange({ bannerImage: attachment.url, bannerPreset: "custom" });
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
      const attachment = await uploadLobbyAttachment(file);
      onAppearanceChange({ iconImage: attachment.url });
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
        </aside>
        <div className="dc-settings-body chat-scroll">
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
                    onClick={() => onAppearanceChange({ bannerPreset: preset, bannerImage: undefined })}
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
                    onAppearanceChange({ iconLabel });
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
                      onAppearanceChange({ notifications: value as RoomAppearance["notifications"] })
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
                  onChange={() => onAppearanceChange({ inviteScope: "room" })}
                />
                초대 링크는 이 방만 표시
              </label>
              <label>
                <input
                  type="radio"
                  name="invite-scope"
                  checked={appearance.inviteScope === "read_only"}
                  onChange={() => onAppearanceChange({ inviteScope: "read_only" })}
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
        </div>
      </section>
    </div>
  );
}
