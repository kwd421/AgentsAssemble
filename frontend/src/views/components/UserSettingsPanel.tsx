import { Camera, Headphones, Mic, MicOff, Palette, UserCircle } from "lucide-react";

import type { UserProfile, UserProfileIdentity } from "../../api";
import GuestRecoverySettings from "./GuestRecoverySettings";
import GoogleAccountSettings from "./GoogleAccountSettings";
import "./UserSettingsPanel.css";

export type UserSettingsSection = "account" | "profile" | "voice" | "recovery";

const USER_SETTINGS_SECTIONS: Array<{
  id: UserSettingsSection;
  label: string;
  helper: string;
}> = [
  { id: "account", label: "계정", helper: "이름, 핸들, 현재 표시 상태" },
  { id: "profile", label: "프로필", helper: "배너, 아바타, 상태 문구" },
  { id: "voice", label: "음성", helper: "마이크와 헤드셋 표시" },
  { id: "recovery", label: "복구", helper: "다른 기기에서 신원 이어가기" },
];

export default function UserSettingsPanel({
  draft,
  saving,
  profileError,
  settingsSection,
  onSectionChange,
  onDraftChange,
  onReset,
  onSave,
  onEditAvatar,
  profileIdentity,
}: {
  draft: UserProfile;
  saving: boolean;
  profileError: string;
  settingsSection: UserSettingsSection;
  onSectionChange: (section: UserSettingsSection) => void;
  onDraftChange: (profile: UserProfile) => void;
  onReset: () => void;
  onSave: () => void;
  onEditAvatar: () => void;
  profileIdentity?: UserProfileIdentity;
}) {
  const sections = profileIdentity?.sessionToken
    ? USER_SETTINGS_SECTIONS
    : USER_SETTINGS_SECTIONS.filter((section) => section.id !== "recovery");
  return (
    <div className="dc-user-settings-panel" aria-label="사용자 설정">
      <div className="dc-user-settings-shell">
        <nav className="dc-user-settings-nav" aria-label="사용자 설정 섹션">
          {sections.map((section) => (
            <button
              key={section.id}
              type="button"
              aria-current={settingsSection === section.id ? "page" : undefined}
              onClick={() => onSectionChange(section.id)}
            >
              <span>{section.label}</span>
              <small>{section.helper}</small>
            </button>
          ))}
        </nav>

        <section className="dc-user-settings-section">
          {settingsSection === "account" && (
            <>
              <header>
                <UserCircle size={18} />
                <div>
                  <h3>계정</h3>
                  <p>서버와 방에서 함께 사용하는 내 사용자 정보를 저장합니다.</p>
                </div>
              </header>
              <div className="dc-user-settings-grid">
                <label>
                  표시 이름
                  <input
                    value={draft.displayName}
                    onChange={(event) => onDraftChange({ ...draft, displayName: event.target.value })}
                    maxLength={120}
                  />
                </label>
                <label>
                  핸들
                  <input
                    value={draft.handle}
                    onChange={(event) => onDraftChange({ ...draft, handle: event.target.value })}
                    maxLength={120}
                  />
                </label>
                <label>
                  상태
                  <select
                    value={draft.status}
                    onChange={(event) =>
                      onDraftChange({
                        ...draft,
                        status: event.target.value as UserProfile["status"],
                      })
                    }
                  >
                    <option value="online">온라인</option>
                    <option value="idle">자리 비움</option>
                    <option value="dnd">방해 금지</option>
                    <option value="offline">오프라인 표시</option>
                  </select>
                </label>
              </div>
              <GoogleAccountSettings identity={profileIdentity || {}} />
            </>
          )}

          {settingsSection === "profile" && (
            <>
              <header>
                <Palette size={18} />
                <div>
                  <h3>프로필</h3>
                  <p>Discord식 카드에 표시되는 배너와 짧은 상태를 조정합니다.</p>
                </div>
              </header>
              <div className="dc-user-settings-grid">
                <button
                  type="button"
                  className="dc-user-settings-avatar-action"
                  onClick={onEditAvatar}
                  aria-label="프로필 사진 변경"
                >
                  <span
                    className="dc-user-settings-avatar-preview"
                    data-has-image={Boolean(draft.avatarImage)}
                    style={
                      draft.avatarImage
                        ? { backgroundImage: `url(${draft.avatarImage})` }
                        : undefined
                    }
                    aria-hidden
                  >
                    {draft.avatarImage ? null : draft.avatarLabel}
                  </span>
                  <span>
                    <strong>프로필 사진 변경</strong>
                    <small>이미지를 선택하고 표시 영역을 조정합니다.</small>
                  </span>
                  <Camera size={17} aria-hidden />
                </button>
                <label>
                  사용자 지정 상태
                  <input
                    value={draft.customStatus}
                    onChange={(event) => onDraftChange({ ...draft, customStatus: event.target.value })}
                    maxLength={160}
                  />
                </label>
                <label>
                  배너
                  <select
                    value={draft.bannerPreset}
                    onChange={(event) =>
                      onDraftChange({
                        ...draft,
                        bannerPreset: event.target.value as UserProfile["bannerPreset"],
                      })
                    }
                  >
                    <option value="default">Discord blue</option>
                    <option value="forest">Forest</option>
                    <option value="midnight">Midnight</option>
                    <option value="ember">Ember</option>
                    <option value="custom">사용자 색상</option>
                  </select>
                </label>
                <label>
                  아바타 라벨
                  <input
                    value={draft.avatarLabel}
                    onChange={(event) => onDraftChange({ ...draft, avatarLabel: event.target.value })}
                    maxLength={2}
                  />
                </label>
                <label>
                  포인트 색상
                  <input
                    type="color"
                    value={draft.accentColor}
                    onChange={(event) => onDraftChange({ ...draft, accentColor: event.target.value })}
                  />
                </label>
              </div>
            </>
          )}

          {settingsSection === "voice" && (
            <>
              <header>
                <Headphones size={18} />
                <div>
                  <h3>음성</h3>
                  <p>실제 음성 연결은 아니고, 방 클라이언트의 표시 상태만 저장합니다.</p>
                </div>
              </header>
              <div className="dc-user-settings-toggles">
                <button
                  type="button"
                  aria-pressed={draft.micMuted}
                  onClick={() => onDraftChange({ ...draft, micMuted: !draft.micMuted })}
                >
                  {draft.micMuted ? <MicOff size={18} /> : <Mic size={18} />}
                  <span>{draft.micMuted ? "마이크 음소거 표시" : "마이크 켜짐 표시"}</span>
                </button>
                <button
                  type="button"
                  aria-pressed={draft.deafened}
                  onClick={() => onDraftChange({ ...draft, deafened: !draft.deafened })}
                >
                  <Headphones size={18} />
                  <span>{draft.deafened ? "헤드셋 차단 표시" : "헤드셋 사용 표시"}</span>
                </button>
              </div>
            </>
          )}

          {settingsSection === "recovery" && profileIdentity?.sessionToken && (
            <GuestRecoverySettings identity={profileIdentity} />
          )}
        </section>
      </div>
      {profileError && <p className="dc-user-settings-error">{profileError}</p>}
      {settingsSection !== "recovery" && (
        <div className="dc-user-settings-actions">
          <button type="button" onClick={onReset} disabled={saving}>
            되돌리기
          </button>
          <button type="button" onClick={onSave} disabled={saving}>
            {saving ? "저장 중" : "저장"}
          </button>
        </div>
      )}
    </div>
  );
}
