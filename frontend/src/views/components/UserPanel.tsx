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
import UserSettingsPanel, { type UserSettingsSection } from "./UserSettingsPanel";

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
  const [settingsSection, setSettingsSection] = useState<UserSettingsSection>("account");
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

  function openSettings(section: UserSettingsSection = "account") {
    setDraft(profile);
    setProfileOpen(true);
    setSettingsOpen(true);
    setSettingsSection(section);
  }

  function reportLocalOnlyAction(message: string) {
    setProfileError(message);
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
            <div className="dc-profile-card-title">
              <div>
                <span>프로필 및 상태 관리</span>
                <h2>{profile.displayName}</h2>
              </div>
              <button type="button" onClick={() => openSettings("profile")}>
                전체 프로필 보기
              </button>
            </div>
            <p>{profile.handle}</p>
            <div className="dc-profile-badges">
              <span>{profile.customStatus}</span>
              <span>#room-client</span>
            </div>
            <button
              type="button"
              className="dc-profile-status-row"
              onClick={() => openSettings("profile")}
            >
              <span className="dc-profile-status-add">+</span>
              <span>
                <strong>사용자 지정 상태</strong>
                <small>{profile.customStatus || "방금 플레이를 마쳤어요..."}</small>
              </span>
            </button>
            <div className="dc-profile-card-actions">
              <button type="button" onClick={() => openSettings("profile")}>
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
              <UserSettingsPanel
                draft={draft}
                saving={saving}
                profileError={profileError}
                settingsSection={settingsSection}
                onSectionChange={setSettingsSection}
                onDraftChange={setDraft}
                onReset={() => setDraft(profile)}
                onSave={() => void saveDraft()}
              />
            )}
            <div className="dc-profile-menu">
              <button type="button" onClick={() => openSettings("account")}>
                <span className={`dc-profile-menu-dot ${statusClass}`} aria-hidden />
                내 상태: {profile.status === "offline" ? "오프라인 표시" : backendStatusText}
                <ChevronDown size={16} />
              </button>
              <button
                type="button"
                onClick={() => reportLocalOnlyAction("계정 바꾸기는 로컬 프로필만 지원합니다.")}
              >
                <UserPen size={17} />
                계정 바꾸기
                <ChevronDown size={16} />
              </button>
              <button
                type="button"
                onClick={() => reportLocalOnlyAction("더 많은 옵션은 외부 Discord에 연결하지 않습니다.")}
              >
                <Settings size={17} />
                더 많은 옵션
                <ChevronDown size={16} />
              </button>
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
              <button type="button" onClick={() => openSettings("account")}>
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
          <button type="button" aria-label="사용자 설정" onClick={() => openSettings("account")}>
            <Settings size={16} />
          </button>
        </div>
      </div>
    </div>
  );
}
