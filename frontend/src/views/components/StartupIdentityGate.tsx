import { useEffect, useState } from "react";
import { ArrowRight, LoaderCircle, UserRound } from "lucide-react";

import { fetchAccountStatus } from "../../api/identity";
import { saveUserProfile } from "../../api/room";
import { rememberGuestProfile } from "../../lib/deviceIdentity";
import { DEFAULT_USER_PROFILE } from "../../lib/userProfileModel";
import GoogleAccountSettings from "./GoogleAccountSettings";

export default function StartupIdentityGate({
  deviceToken,
  onComplete,
}: {
  deviceToken: string;
  onComplete: () => void;
}) {
  const [displayName, setDisplayName] = useState("");
  const [checking, setChecking] = useState(true);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let active = true;
    fetchAccountStatus({ deviceToken })
      .then((account) => {
        if (!active) return;
        if (account.account) {
          onComplete();
          return;
        }
        setChecking(false);
      })
      .catch(() => {
        if (active) setChecking(false);
      });
    return () => {
      active = false;
    };
  }, [deviceToken, onComplete]);

  async function continueAsGuest() {
    const name = displayName.trim();
    if (!name || saving) return;
    setSaving(true);
    rememberGuestProfile({ displayName: name });
    try {
      await saveUserProfile(
        {
          ...DEFAULT_USER_PROFILE,
          displayName: name,
          avatarLabel: name.slice(0, 2).toUpperCase(),
        },
        { deviceToken }
      );
    } catch {
      // Local identity is authoritative while the host is unreachable. The
      // normal profile surface synchronizes again after reconnection.
    }
    onComplete();
  }

  return (
    <div className="fixed inset-0 z-[400] grid place-items-center overflow-y-auto bg-[#101114] p-5">
      <main
        className="grid w-full max-w-[520px] gap-5 rounded-xl border border-white/10 bg-[#202126] p-6 shadow-2xl"
        aria-label="시작 로그인"
      >
        <header className="grid gap-2">
          <span className="text-[11px] font-black uppercase tracking-[0.16em] text-[#8d96ff]">
            AgentsAssemble
          </span>
          <h1 className="text-2xl font-black text-text-primary">어떻게 사용할까요?</h1>
          <p className="text-[13px] font-semibold leading-5 text-text-muted">
            공개 계정으로 이어서 쓰거나, 이 기기에만 남는 게스트 프로필로 바로 시작할 수 있습니다.
          </p>
        </header>

        {checking ? (
          <p className="flex items-center gap-2 rounded-lg bg-[#2b2d31] px-4 py-4 text-[12px] font-bold text-text-muted">
            <LoaderCircle size={16} className="animate-spin" /> 저장된 계정 확인 중
          </p>
        ) : (
          <>
            <GoogleAccountSettings
              identity={{ deviceToken }}
              onAccountConnected={onComplete}
            />
            <div className="flex items-center gap-3 text-[10px] font-black uppercase tracking-wider text-text-muted">
              <span className="h-px flex-1 bg-white/10" /> 또는 <span className="h-px flex-1 bg-white/10" />
            </div>
            <section className="grid gap-3 rounded-lg bg-[#1b1c20] p-4" aria-label="게스트로 시작">
              <div className="flex items-start gap-3">
                <UserRound size={20} className="mt-0.5 shrink-0 text-text-secondary" />
                <div>
                  <h2 className="text-[14px] font-black text-text-primary">게스트로 시작</h2>
                  <p className="mt-1 text-[11px] font-semibold leading-4 text-text-muted">
                    로그인 없이 로컬 방을 사용합니다. 나중에 계정으로 연결할 수 있습니다.
                  </p>
                </div>
              </div>
              <label className="grid gap-1.5 text-[11px] font-black text-text-secondary">
                표시 이름
                <input
                  autoFocus
                  type="text"
                  maxLength={80}
                  value={displayName}
                  placeholder="다른 참가자에게 보일 이름"
                  className="min-h-10 rounded-md border border-transparent bg-[#2b2d31] px-3 text-[13px] font-semibold text-text-primary outline-none focus:border-[#5865f2]"
                  onChange={(event) => setDisplayName(event.currentTarget.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") void continueAsGuest();
                  }}
                />
              </label>
              <button
                type="button"
                className="flex min-h-10 items-center justify-center gap-2 rounded-md bg-[#5865f2] px-4 text-[13px] font-black text-white disabled:opacity-50"
                disabled={!displayName.trim() || saving}
                onClick={() => void continueAsGuest()}
              >
                {saving ? <LoaderCircle size={16} className="animate-spin" /> : <ArrowRight size={16} />}
                {saving ? "준비 중…" : "게스트로 계속"}
              </button>
            </section>
          </>
        )}
      </main>
    </div>
  );
}
