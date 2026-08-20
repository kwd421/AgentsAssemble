import { useEffect, useRef, useState } from "react";
import { CircleUserRound, LoaderCircle } from "lucide-react";

import {
  connectGoogleAccount,
  disconnectGoogleAccount,
  fetchAccountStatus,
  startGoogleAccountLogin,
  type AccountStatusResponse,
  type GoogleAccountChallengeResponse,
} from "../../api/identity";
import type { UserProfileIdentity } from "../../api/room";
import { isDesktopWebview } from "../../lib/desktopBridge";
import { clearRememberedGuestProfile } from "../../lib/deviceIdentity";
import { googleIdentityApi, loadGoogleIdentityScript } from "../../lib/googleIdentity";
import { persistRoomGuestSession } from "../../lib/roomGuestSession";

const ACCOUNT_SWITCH_WARNING =
  "선택한 Google 계정이 이 서버에서 이미 사용 중이면 현재 게스트 프로필, 참여 상태, 복구 코드가 폐기되고 기존 계정으로 전환됩니다. 과거 채팅 기록은 남습니다. 계속할까요?";

function confirmPossibleGuestDiscard(): boolean {
  return window.confirm(ACCOUNT_SWITCH_WARNING);
}

function resetRetiredGuestBrowserState() {
  persistRoomGuestSession(null);
  clearRememberedGuestProfile();
  window.location.reload();
}

export default function GoogleAccountSettings({
  identity,
  onAccountConnected,
}: {
  identity: UserProfileIdentity;
  onAccountConnected?: () => void;
}) {
  const [status, setStatus] = useState<AccountStatusResponse | null>(null);
  const [error, setError] = useState("");
  const [connecting, setConnecting] = useState(false);
  const [challenge, setChallenge] = useState<GoogleAccountChallengeResponse | null>(null);
  const [disconnecting, setDisconnecting] = useState(false);
  const buttonRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let active = true;
    fetchAccountStatus(identity)
      .then((next) => {
        if (active) setStatus(next);
      })
      .catch((reason: Error) => {
        if (active) setError(reason.message || "계정 상태를 불러오지 못했습니다.");
      });
    return () => {
      active = false;
    };
  }, [identity.deviceToken, identity.sessionToken]);

  useEffect(() => {
    if (!status?.google.enabled || status.account || isDesktopWebview() || challenge) return;
    let active = true;
    startGoogleAccountLogin(identity)
      .then((next) => {
        if (active) setChallenge(next);
      })
      .catch((reason: Error) => {
        if (active) setError(reason.message || "Google 로그인을 준비하지 못했습니다.");
      });
    return () => {
      active = false;
    };
  }, [challenge, identity.deviceToken, identity.sessionToken, status?.account, status?.google.enabled]);

  useEffect(() => {
    if (!challenge || status?.account || !buttonRef.current || isDesktopWebview()) return;
    let active = true;
    const target = buttonRef.current;
    loadGoogleIdentityScript()
      .then(() => {
        if (!active || !target) return;
        const api = googleIdentityApi();
        if (!api) throw new Error("Google 로그인 모듈을 사용할 수 없습니다.");
        target.replaceChildren();
        api.initialize({
          client_id: challenge.client_id,
          nonce: challenge.nonce,
          callback: (response) => {
            const credential = String(response.credential || "").trim();
            if (!credential) {
              setError("Google이 로그인 응답을 반환하지 않았습니다.");
              return;
            }
            if (!confirmPossibleGuestDiscard()) return;
            setConnecting(true);
            setError("");
            void connectGoogleAccount({
              credential,
              nonce: challenge.nonce,
              discardGuestOnAccountSwitch: true,
              identity,
            })
              .then((connected) => {
                if (!active) return;
                if (connected.identity_switched) {
                  resetRetiredGuestBrowserState();
                  return;
                }
                setStatus((current) =>
                  current ? { ...current, account: connected.account } : current
                );
                onAccountConnected?.();
              })
              .catch(async (reason: Error) => {
                if (!active) return;
                setError(reason.message || "Google 계정을 연결하지 못했습니다.");
                setChallenge(null);
                const refreshed = await fetchAccountStatus(identity).catch(() => null);
                if (active && refreshed) setStatus(refreshed);
              })
              .finally(() => {
                if (active) setConnecting(false);
              });
          },
        });
        api.renderButton(target, {
          type: "standard",
          theme: "filled_black",
          size: "large",
          text: "continue_with",
          shape: "rectangular",
          logo_alignment: "left",
          width: Math.max(220, Math.floor(target.getBoundingClientRect().width)),
        });
      })
      .catch((reason: Error) => {
        if (active) setError(reason.message || "Google 로그인을 준비하지 못했습니다.");
      });
    return () => {
      active = false;
      googleIdentityApi()?.cancel();
      target.replaceChildren();
    };
  }, [challenge, identity.deviceToken, identity.sessionToken, status?.account]);

  const disconnect = async () => {
    setDisconnecting(true);
    setError("");
    try {
      await disconnectGoogleAccount(identity);
      setStatus((current) => (current ? { ...current, account: null } : current));
      setChallenge(null);
      setConnecting(false);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "공개 계정에서 로그아웃하지 못했습니다.");
    } finally {
      setDisconnecting(false);
    }
  };

  return (
    <section className="mt-4 grid gap-3 rounded-md bg-[#1e1f22] p-3" aria-label="공개 계정 연결">
      <div className="flex items-start gap-2 text-text-secondary">
        <CircleUserRound size={18} className="mt-0.5 shrink-0" />
        <div>
          <h4 className="text-[13px] font-black text-text-primary">공개 계정</h4>
          <p className="mt-1 text-[11px] font-bold leading-4 text-text-muted">
            기기 자격과 별개의 계정 ID를 만들고 이 서버의 사용자 신원에 명시적으로 연결합니다.
          </p>
        </div>
      </div>

      {!status && !error && (
        <p className="flex items-center gap-2 text-[11px] font-bold text-text-muted">
          <LoaderCircle size={14} className="animate-spin" /> 계정 상태 확인 중
        </p>
      )}

      {status?.account && (
        <div className="grid gap-3 rounded-md bg-[#2b2d31] px-3 py-3">
          <div className="grid gap-1">
            <strong className="text-[13px] text-text-primary">
              {status.account.display_name || "Google 계정"}
            </strong>
            <span className="text-[11px] font-bold text-text-muted">{status.account.email}</span>
          </div>
          <div className="flex items-center justify-between gap-3 border-t border-white/5 pt-3">
            <p className="text-[10px] font-bold leading-4 text-text-muted">
              로그아웃해도 로컬 프로필과 방은 유지됩니다.
            </p>
            <button
              type="button"
              aria-label="공개 계정 로그아웃"
              className="shrink-0 rounded-md bg-[#3a3c42] px-3 py-2 text-[11px] font-black text-[#ffb4b5] hover:bg-[#45474e] disabled:opacity-60"
              disabled={disconnecting}
              onClick={() => void disconnect()}
            >
              {disconnecting ? "로그아웃 중…" : "로그아웃"}
            </button>
          </div>
        </div>
      )}

      {status && !status.account && !status.google.enabled && (
        <p className="text-[11px] font-bold text-text-muted">
          이 서버에는 아직 Google 로그인이 설정되지 않았습니다.
        </p>
      )}

      {status?.google.enabled && !status.account && isDesktopWebview() && (
        <p className="text-[11px] font-bold leading-4 text-text-muted">
          데스크톱 Google 로그인은 앱 시작 화면의 중앙 계정에서 관리합니다.
        </p>
      )}

      {status?.google.enabled && !status.account && !isDesktopWebview() && (
        <div className={connecting ? "pointer-events-none opacity-60" : ""}>
          <div ref={buttonRef} className="min-h-10 w-full overflow-hidden" />
        </div>
      )}

      {error && <p className="text-[11px] font-bold leading-4 text-[#ff8b8d]">{error}</p>}
    </section>
  );
}
