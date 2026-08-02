import { useEffect, useRef, useState } from "react";
import { CheckCircle2, LoaderCircle } from "lucide-react";

import {
  completeGoogleAccountHandoff,
  configureGoogleAccountHandoff,
  type PublicAccount,
} from "../api/identity";
import { googleIdentityApi, loadGoogleIdentityScript } from "../lib/googleIdentity";

export function consumeGoogleAccountHandoffToken(url = window.location.href): string {
  const parsed = new URL(url);
  const token = new URLSearchParams(parsed.hash.replace(/^#/, "")).get(
    "google_handoff"
  );
  if (token && url === window.location.href) {
    parsed.hash = "";
    window.history.replaceState({}, "", `${parsed.pathname}${parsed.search}`);
  }
  return String(token || "").trim();
}

export default function GoogleAccountHandoffPage({ token }: { token: string }) {
  const buttonRef = useRef<HTMLDivElement>(null);
  const [message, setMessage] = useState("Google 로그인을 준비하는 중…");
  const [error, setError] = useState("");
  const [account, setAccount] = useState<PublicAccount | null>(null);

  useEffect(() => {
    let active = true;
    const target = buttonRef.current;
    if (!token || !target) {
      setError("로그인 링크가 없거나 올바르지 않습니다.");
      return;
    }
    void configureGoogleAccountHandoff(token)
      .then(async (configuration) => {
        await loadGoogleIdentityScript();
        if (!active) return;
        const api = googleIdentityApi();
        if (!api) throw new Error("Google 로그인 모듈을 사용할 수 없습니다.");
        setMessage("연결할 Google 계정을 선택하세요.");
        api.initialize({
          client_id: configuration.client_id,
          nonce: configuration.nonce,
          callback: (response) => {
            const credential = String(response.credential || "").trim();
            if (!credential) {
              setError("Google이 로그인 응답을 반환하지 않았습니다.");
              return;
            }
            setMessage("AgentsAssemble 계정에 연결하는 중…");
            void completeGoogleAccountHandoff({ token, credential })
              .then((connected) => {
                if (!active) return;
                setAccount(connected.account);
                setMessage("Google 계정 연결이 완료됐습니다.");
              })
              .catch((reason: Error) => {
                if (active) setError(reason.message || "Google 계정을 연결하지 못했습니다.");
              });
          },
        });
        target.replaceChildren();
        api.renderButton(target, {
          type: "standard",
          theme: "filled_black",
          size: "large",
          text: "continue_with",
          shape: "rectangular",
          logo_alignment: "left",
          width: 320,
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
  }, [token]);

  return (
    <main className="grid min-h-screen place-items-center bg-[#111214] p-6 text-text-primary">
      <section className="grid w-full max-w-md gap-5 rounded-xl border border-[#3f4147] bg-[#232428] p-7 shadow-2xl">
        <div>
          <p className="text-[11px] font-black uppercase tracking-[0.18em] text-[#949ba4]">
            AgentsAssemble
          </p>
          <h1 className="mt-2 text-2xl font-black">Google 계정 연결</h1>
          <p className="mt-2 text-sm font-semibold text-[#b5bac1]">{message}</p>
        </div>
        {account ? (
          <div className="flex items-center gap-3 rounded-lg bg-[#1e1f22] p-4">
            <CheckCircle2 className="text-[#23a55a]" />
            <div className="min-w-0">
              <strong className="block truncate">{account.display_name || "Google 계정"}</strong>
              <span className="block truncate text-xs text-[#949ba4]">{account.email}</span>
            </div>
          </div>
        ) : (
          <>
            <div ref={buttonRef} className="min-h-10 w-full overflow-hidden" />
            {!error && (
              <p className="flex items-center gap-2 text-xs font-bold text-[#949ba4]">
                <LoaderCircle size={14} className="animate-spin" /> 이 창을 닫지 마세요.
              </p>
            )}
          </>
        )}
        {error && <p className="text-sm font-bold text-[#ff8b8d]">{error}</p>}
      </section>
    </main>
  );
}
