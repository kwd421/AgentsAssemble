import { type CSSProperties, useEffect, useRef, useState } from "react";
import {
  ChevronDown,
  Headphones,
  Mic,
  MicOff,
  Settings,
  UserPen,
  X,
} from "lucide-react";

import { fetchUserProfile, saveUserProfile, type UserProfile } from "../../api";

const DEFAULT_USER_PROFILE: UserProfile = {
  displayName: "SeiNel",
  handle: "seinel.",
  status: "online",
  customStatus: "AgentsAssemble",
  avatarLabel: "나",
  bannerPreset: "default",
  accentColor: "#5865f2",
  micMuted: true,
  deafened: false,
};

function profileStatusClass(profile: UserProfile, hasBackendError: boolean) {
  if (hasBackendError || profile.status === "offline") return "offline";
  if (profile.status === "idle") return "idle";
  if (profile.status === "dnd") return "dnd";
  return "online";
}

function profileCssVars(profile: UserProfile): CSSProperties {
  return { "--profile-accent": profile.accentColor } as CSSProperties;
}

function saveDisplayNameForComposers(profile: UserProfile) {
  window.localStorage.setItem("agentsassemble.name", profile.displayName);
}

export default function UserPanel({
  backendStatusText,
  onlineCount,
  agentCount,
  hasBackendError,
}: {
  backendStatusText: string;
  onlineCount: number;
  agentCount: number;
  hasBackendError: boolean;
}) {
  const [profile, setProfile] = useState<UserProfile>(DEFAULT_USER_PROFILE);
  const [draft, setDraft] = useState<UserProfile>(DEFAULT_USER_PROFILE);
  const [profileOpen, setProfileOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [profileError, setProfileError] = useState("");
  const rootRef = useRef<HTMLDivElement>(null);
  const statusClass = profileStatusClass(profile, hasBackendError);

  useEffect(() => {
    let ignore = false;
    fetchUserProfile()
      .then((loadedProfile) => {
        if (ignore) return;
        setProfile(loadedProfile);
        setDraft(loadedProfile);
        saveDisplayNameForComposers(loadedProfile);
      })
      .catch((error: Error) => {
        if (ignore) return;
        setProfileError(error.message || "프로필을 불러오지 못했습니다.");
      });
    return () => {
      ignore = true;
    };
  }, []);

  useEffect(() => {
    if (!profileOpen) return;
    function closeOnOutside(event: MouseEvent) {
      if (!rootRef.current?.contains(event.target as Node)) {
        setProfileOpen(false);
        setSettingsOpen(false);
      }
    }
    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setProfileOpen(false);
        setSettingsOpen(false);
      }
    }
    window.addEventListener("mousedown", closeOnOutside);
    window.addEventListener("keydown", closeOnEscape);
    return () => {
      window.removeEventListener("mousedown", closeOnOutside);
      window.removeEventListener("keydown", closeOnEscape);
    };
  }, [profileOpen]);

  function openProfile() {
    setDraft(profile);
    setProfileOpen((value) => !value);
    setSettingsOpen(false);
  }

  function openSettings() {
    setDraft(profile);
    setProfileOpen(true);
    setSettingsOpen(true);
  }

  async function persistProfile(nextProfile: UserProfile) {
    setProfile(nextProfile);
    setDraft(nextProfile);
    saveDisplayNameForComposers(nextProfile);
    setSaving(true);
    setProfileError("");
    try {
      const savedProfile = await saveUserProfile(nextProfile);
      setProfile(savedProfile);
      setDraft(savedProfile);
      saveDisplayNameForComposers(savedProfile);
    } catch (error) {
      setProfileError(error instanceof Error ? error.message : "프로필을 저장하지 못했습니다.");
    } finally {
      setSaving(false);
    }
  }

  function updateProfileFlag(key: "micMuted" | "deafened", value: boolean) {
    void persistProfile({ ...profile, [key]: value });
  }

  async function saveDraft() {
    await persistProfile(draft);
    setSettingsOpen(false);
  }

  return (
    <div className="dc-user-panel" ref={rootRef} style={profileCssVars(profile)}>
      {profileOpen && (
        <section className="dc-profile-card" aria-label="내 프로필">
          <div
            className="dc-profile-banner"
            data-preset={profile.bannerPreset}
            style={profileCssVars(profile)}
          />
          <button
            type="button"
            className="dc-profile-close"
            onClick={() => {
              setProfileOpen(false);
              setSettingsOpen(false);
            }}
            aria-label="프로필 닫기"
          >
            <X size={16} />
          </button>
          <span className="dc-profile-avatar-wrap">
            <span className="dc-profile-avatar">{profile.avatarLabel}</span>
            <span className={`dc-profile-status ${statusClass}`} aria-hidden />
          </span>
          <div className="dc-profile-body">
            <h2>{profile.displayName}</h2>
            <p>{profile.handle}</p>
            <div className="dc-profile-badges">
              <span>{profile.customStatus}</span>
              <span>#room-client</span>
            </div>
            <div className="dc-profile-card-actions">
              <button type="button" onClick={openSettings}>
                <UserPen size={15} />
                프로필 편집
              </button>
              <button
                type="button"
                onClick={() => {
                  setProfileOpen(false);
                  setSettingsOpen(false);
                }}
              >
                <X size={15} />
                닫기
              </button>
            </div>
            {settingsOpen && (
              <div className="dc-user-settings-panel" aria-label="사용자 설정">
                <div className="dc-user-settings-grid">
                  <label>
                    표시 이름
                    <input
                      value={draft.displayName}
                      onChange={(event) => setDraft({ ...draft, displayName: event.target.value })}
                      maxLength={120}
                    />
                  </label>
                  <label>
                    핸들
                    <input
                      value={draft.handle}
                      onChange={(event) => setDraft({ ...draft, handle: event.target.value })}
                      maxLength={120}
                    />
                  </label>
                  <label>
                    상태
                    <select
                      value={draft.status}
                      onChange={(event) =>
                        setDraft({ ...draft, status: event.target.value as UserProfile["status"] })
                      }
                    >
                      <option value="online">온라인</option>
                      <option value="idle">자리 비움</option>
                      <option value="dnd">방해 금지</option>
                      <option value="offline">오프라인 표시</option>
                    </select>
                  </label>
                  <label>
                    배너
                    <select
                      value={draft.bannerPreset}
                      onChange={(event) =>
                        setDraft({
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
                    한 줄 상태
                    <input
                      value={draft.customStatus}
                      onChange={(event) =>
                        setDraft({ ...draft, customStatus: event.target.value })
                      }
                      maxLength={160}
                    />
                  </label>
                  <label>
                    아바타 라벨
                    <input
                      value={draft.avatarLabel}
                      onChange={(event) => setDraft({ ...draft, avatarLabel: event.target.value })}
                      maxLength={2}
                    />
                  </label>
                  <label>
                    포인트 색상
                    <input
                      type="color"
                      value={draft.accentColor}
                      onChange={(event) => setDraft({ ...draft, accentColor: event.target.value })}
                    />
                  </label>
                </div>
                {profileError && <p className="dc-user-settings-error">{profileError}</p>}
                <div className="dc-user-settings-actions">
                  <button type="button" onClick={() => setDraft(profile)} disabled={saving}>
                    되돌리기
                  </button>
                  <button type="button" onClick={saveDraft} disabled={saving}>
                    {saving ? "저장 중" : "저장"}
                  </button>
                </div>
              </div>
            )}
            <div className="dc-profile-menu">
              <button type="button" onClick={() => updateProfileFlag("micMuted", !profile.micMuted)}>
                {profile.micMuted ? <MicOff size={17} /> : <Mic size={17} />}
                {profile.micMuted ? "마이크 음소거 해제" : "마이크 음소거"}
                <ChevronDown size={16} />
              </button>
              <button type="button" onClick={() => updateProfileFlag("deafened", !profile.deafened)}>
                <Headphones size={17} />
                {profile.deafened ? "헤드셋 켜기" : "헤드셋 끄기"}
                <ChevronDown size={16} />
              </button>
              <button type="button" onClick={openSettings}>
                <Settings size={17} />
                사용자 설정
                <ChevronDown size={16} />
              </button>
            </div>
          </div>
        </section>
      )}

      <div className="dc-current-user">
        <button
          type="button"
          className="dc-user-identity"
          onClick={openProfile}
          aria-expanded={profileOpen}
        >
          <span className="relative shrink-0">
            <span className="dc-self-avatar">{profile.avatarLabel}</span>
            <span
              className={`absolute -bottom-0.5 -right-0.5 h-3 w-3 rounded-full border-2 border-sidebar ${
                statusClass === "offline"
                  ? "bg-danger"
                  : statusClass === "idle"
                    ? "bg-yellow-400"
                    : statusClass === "dnd"
                      ? "bg-red-500"
                      : "bg-online"
              }`}
              aria-hidden
            />
          </span>
          <span className="min-w-0 flex-1 text-left">
            <span className="block truncate text-[13px] font-bold text-text-primary">
              {profile.displayName}
            </span>
            <span className="flex items-center gap-1 truncate text-[11px] text-text-muted">
              <span
                className={`h-1.5 w-1.5 rounded-full ${
                  statusClass === "offline"
                    ? "bg-danger"
                    : statusClass === "idle"
                      ? "bg-yellow-400"
                      : statusClass === "dnd"
                        ? "bg-red-500"
                        : "bg-online"
                }`}
                aria-hidden
              />
              {backendStatusText} · {onlineCount}/{agentCount}
            </span>
          </span>
        </button>
        <div className="dc-user-actions">
          <button
            type="button"
            aria-label={profile.micMuted ? "마이크 음소거 해제" : "마이크 음소거"}
            aria-pressed={profile.micMuted}
            data-danger={profile.micMuted}
            onClick={() => updateProfileFlag("micMuted", !profile.micMuted)}
          >
            {profile.micMuted ? <MicOff size={16} /> : <Mic size={16} />}
          </button>
          <button
            type="button"
            aria-label={profile.deafened ? "헤드셋 켜기" : "헤드셋 끄기"}
            aria-pressed={profile.deafened}
            onClick={() => updateProfileFlag("deafened", !profile.deafened)}
          >
            <Headphones size={16} />
          </button>
          <button type="button" aria-label="사용자 설정" onClick={openSettings}>
            <Settings size={16} />
          </button>
        </div>
      </div>
    </div>
  );
}
