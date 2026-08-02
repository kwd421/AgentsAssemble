export type GoogleCredentialResponse = { credential?: string };

export type GoogleIdentityApi = {
  initialize(options: {
    client_id: string;
    nonce: string;
    callback: (response: GoogleCredentialResponse) => void;
  }): void;
  renderButton(
    target: HTMLElement,
    options: Record<string, string | number | boolean>
  ): void;
  cancel(): void;
};

export function googleIdentityApi(): GoogleIdentityApi | undefined {
  return (
    window as typeof window & {
      google?: { accounts?: { id?: GoogleIdentityApi } };
    }
  ).google?.accounts?.id;
}

let googleScriptPromise: Promise<void> | null = null;

export function loadGoogleIdentityScript(): Promise<void> {
  if (googleIdentityApi()) return Promise.resolve();
  if (googleScriptPromise) return googleScriptPromise;
  const pending = new Promise<void>((resolve, reject) => {
    const existing = document.querySelector<HTMLScriptElement>(
      'script[data-agentsassemble-google-identity="true"]'
    );
    const script = existing || document.createElement("script");
    const onLoad = () =>
      googleIdentityApi()
        ? resolve()
        : reject(new Error("Google 로그인 모듈을 불러오지 못했습니다."));
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
