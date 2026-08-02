import { useEffect, useRef, useState } from "react";
import { CircleUserRound, LoaderCircle } from "lucide-react";

import {
  connectGoogleAccount,
  fetchAccountStatus,
  type AccountStatusResponse,
} from "../../api/identity";
import type { UserProfileIdentity } from "../../api/room";


type CredentialResponse = { credential?: string };
type GoogleIdentityApi = {
  initialize(options: {
    client_id: string;
    nonce: string;
    callback: (response: CredentialResponse) => void;
  }): void;
  renderButton(
    target: HTMLElement,
    options: Record<string, string | number | boolean>
  ): void;
  cancel(): void;
};

function googleIdentityApi(): GoogleIdentityApi | undefined {
  return (
    window as typeof window & {
      google?: { accounts?: { id?: GoogleIdentityApi } };
    }
  ).google?.accounts?.id;
}

let googleScriptPromise: Promise<void> | null = null;

function loadGoogleIdentityScript(): Promise<void> {
  if (googleIdentityApi()) return Promise.resolve();
  if (googleScriptPromise) return googleScriptPromise;
  const pending = new Promise<void>((resolve, reject) => {
    const existing = document.querySelector<HTMLScriptElement>(
      'script[data-agentsassemble-google-identity="true"]'
    );
    const script = existing || document.createElement("script");
    const onLoad = () => (googleIdentityApi() ? resolve() : reject(new Error("Google 로그인 모듈을 불러오지 못했습니다.")));
    script.addEventListener("load", onLoad, { once: true });
    script.addEventListener(
      "error",
      () => reject(new Error("Google 로그인 모듈을 불러오지 못했습니다.")),
      { once: true }
    );
    if (!existing) {
      script.src = "https://accounts.google.com/gsi/client";
      script.async = true;
      script.dataset.agentsassembleGoogleIdentity = "true";
      document.head.append(script);
    }
  }).catch((error) => {
    googleScriptPromise = null;
    throw error;
  });
  googleScriptPromise = pending;
  return pending;
}

function isDesktopWebview(): boolean {
  return "__TAURI_INTERNALS__" in window;
}

export default function GoogleAccountSettings({
  identity,
}: {
  identity: UserProfileIdentity;
}) {
  const [status, setStatus] = useState<AccountStatusResponse | null>(null);
  const [error, setError] = useState("");
  const [connecting, setConnecting] = useState(false);
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
    if (!status?.google.enabled || status.account || !buttonRef.current || isDesktopWebview()) return;
    let active = true;
    const target = buttonRef.current;
    loadGoogleIdentityScript()
      .then(() => {
        if (!active || !target) return;
        const api = googleIdentityApi();
        if (!api) throw new Error("Google 로그인 모듈을 사용할 수 없습니다.");
        target.replaceChildren();
        api.initialize({
          client_id: status.google.client_id,
          nonce: status.google.nonce,
          callback: (response) => {
            const credential = String(response.credential || "").trim();
            if (!credential) {
              setError("Google이 로그인 응답을 반환하지 않았습니다.");
              return;
            }
            setConnecting(true);
            setError("");
            void connectGoogleAccount({
              credential,
              nonce: status.google.nonce,
              identity,
            })
              .then((connected) => {
                if (!active) return;
                setStatus((current) =>
                  current ? { ...current, account: connected.account } : current
                );
              })
              .catch(async (reason: Error) => {
                if (!active) return;
                setError(reason.message || "Google 계정을 연결하지 못했습니다.");
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
  }, [identity.deviceToken, identity.sessionToken, status?.account, status?.google.client_id, status?.google.enabled, status?.google.nonce]);

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
        <div className="grid gap-1 rounded-md bg-[#2b2d31] px-3 py-2">
          <strong className="text-[13px] text-text-primary">
            {status.account.display_name || "Google 계정"}
          </strong>
          <span className="text-[11px] font-bold text-text-muted">{status.account.email}</span>
          <code className="truncate text-[10px] text-text-muted">{status.account.account_id}</code>
        </div>
      )}

      {status && !status.account && !status.google.enabled && (
        <p className="text-[11px] font-bold text-text-muted">
          이 서버에는 아직 Google 로그인이 설정되지 않았습니다.
        </p>
      )}

      {status?.google.enabled && !status.account && isDesktopWebview() && (
        <p className="text-[11px] font-bold leading-4 text-[#f0b232]">
          Google은 앱 내 WebView 로그인을 허용하지 않습니다. 시스템 브라우저 연결 경로가 준비되기 전에는 웹에서 연결해 주세요.
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
