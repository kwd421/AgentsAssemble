import { useEffect, useRef, useState } from "react";
import {
  ChevronDown,
  Headphones,
  Mic,
  MicOff,
  Settings,
  UserPen,
  X,
} from "lucide-react";

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
  const [profileOpen, setProfileOpen] = useState(false);
  const [micMuted, setMicMuted] = useState(true);
  const [deafened, setDeafened] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!profileOpen) return;
    function closeOnOutside(event: MouseEvent) {
      if (!rootRef.current?.contains(event.target as Node)) setProfileOpen(false);
    }
    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === "Escape") setProfileOpen(false);
    }
    window.addEventListener("mousedown", closeOnOutside);
    window.addEventListener("keydown", closeOnEscape);
    return () => {
      window.removeEventListener("mousedown", closeOnOutside);
      window.removeEventListener("keydown", closeOnEscape);
    };
  }, [profileOpen]);

  return (
    <div className="dc-user-panel" ref={rootRef}>
      {profileOpen && (
        <section className="dc-profile-card" aria-label="내 프로필">
          <div className="dc-profile-banner" />
          <button
            type="button"
            className="dc-profile-close"
            onClick={() => setProfileOpen(false)}
            aria-label="프로필 닫기"
          >
            <X size={16} />
          </button>
          <span className="dc-profile-avatar-wrap">
            <span className="dc-profile-avatar">나</span>
            <span
              className={`dc-profile-status ${hasBackendError ? "offline" : "online"}`}
              aria-hidden
            />
          </span>
          <div className="dc-profile-body">
            <h2>SeiNel</h2>
            <p>seinel.</p>
            <div className="dc-profile-badges">
              <span>AgentsAssemble</span>
              <span>#room-client</span>
            </div>
            <div className="dc-profile-card-actions">
              <button type="button">
                <UserPen size={15} />
                프로필 편집
              </button>
              <button type="button" onClick={() => setProfileOpen(false)}>
                <X size={15} />
                닫기
              </button>
            </div>
            <div className="dc-profile-menu">
              <button type="button" onClick={() => setMicMuted((value) => !value)}>
                {micMuted ? <MicOff size={17} /> : <Mic size={17} />}
                {micMuted ? "마이크 음소거 해제" : "마이크 음소거"}
                <ChevronDown size={16} />
              </button>
              <button type="button" onClick={() => setDeafened((value) => !value)}>
                <Headphones size={17} />
                {deafened ? "헤드셋 켜기" : "헤드셋 끄기"}
                <ChevronDown size={16} />
              </button>
              <button type="button">
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
          onClick={() => setProfileOpen((value) => !value)}
          aria-expanded={profileOpen}
        >
          <span className="relative shrink-0">
            <span className="dc-self-avatar">나</span>
            <span
              className={`absolute -bottom-0.5 -right-0.5 h-3 w-3 rounded-full border-2 border-sidebar ${
                hasBackendError ? "bg-danger" : "bg-online"
              }`}
              aria-hidden
            />
          </span>
          <span className="min-w-0 flex-1 text-left">
            <span className="block truncate text-[13px] font-bold text-text-primary">SeiNel</span>
            <span className="flex items-center gap-1 truncate text-[11px] text-text-muted">
              <span
                className={`h-1.5 w-1.5 rounded-full ${hasBackendError ? "bg-danger" : "bg-online"}`}
                aria-hidden
              />
              {backendStatusText} · {onlineCount}/{agentCount}
            </span>
          </span>
        </button>
        <div className="dc-user-actions">
          <button
            type="button"
            aria-label={micMuted ? "마이크 음소거 해제" : "마이크 음소거"}
            aria-pressed={micMuted}
            data-danger={micMuted}
            onClick={() => setMicMuted((value) => !value)}
          >
            {micMuted ? <MicOff size={16} /> : <Mic size={16} />}
          </button>
          <button
            type="button"
            aria-label={deafened ? "헤드셋 켜기" : "헤드셋 끄기"}
            aria-pressed={deafened}
            onClick={() => setDeafened((value) => !value)}
          >
            <Headphones size={16} />
          </button>
          <button
            type="button"
            aria-label="사용자 설정"
            onClick={() => setProfileOpen((value) => !value)}
          >
            <Settings size={16} />
          </button>
        </div>
      </div>
    </div>
  );
}
