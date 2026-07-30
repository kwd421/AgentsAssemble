import { RefreshCw } from "lucide-react";
import { useEffect, useRef, useState } from "react";

const CLIENT_PROTOCOL_VERSION = 1;
const VERSION_POLL_INTERVAL_MS = 15_000;

type RuntimeVersion = {
  frontend_version: string;
  protocol_version: number;
  generation: number;
};

export default function FrontendUpdateNotice() {
  const initialVersion = useRef("");
  const [update, setUpdate] = useState<RuntimeVersion | null>(null);

  useEffect(() => {
    let disposed = false;

    const checkVersion = async () => {
      try {
        const response = await fetch("/api/runtime/version", {
          cache: "no-store",
          credentials: "same-origin",
        });
        if (!response.ok) return;
        const payload = (await response.json()) as Partial<RuntimeVersion>;
        const next: RuntimeVersion = {
          frontend_version: String(payload.frontend_version || "unavailable"),
          protocol_version: Number(payload.protocol_version || CLIENT_PROTOCOL_VERSION),
          generation: Number(payload.generation || 0),
        };
        if (disposed || next.frontend_version === "unavailable") return;
        if (!initialVersion.current) {
          initialVersion.current = next.frontend_version;
          if (next.protocol_version !== CLIENT_PROTOCOL_VERSION) setUpdate(next);
          return;
        }
        if (
          next.frontend_version !== initialVersion.current ||
          next.protocol_version !== CLIENT_PROTOCOL_VERSION
        ) {
          setUpdate(next);
        }
      } catch {
        // WebSocket reconnection owns transient server handoff failures.
      }
    };

    void checkVersion();
    const timer = window.setInterval(checkVersion, VERSION_POLL_INTERVAL_MS);
    const checkVisibleVersion = () => {
      if (document.visibilityState === "visible") void checkVersion();
    };
    window.addEventListener("focus", checkVisibleVersion);
    document.addEventListener("visibilitychange", checkVisibleVersion);
    return () => {
      disposed = true;
      window.clearInterval(timer);
      window.removeEventListener("focus", checkVisibleVersion);
      document.removeEventListener("visibilitychange", checkVisibleVersion);
    };
  }, []);

  if (!update) return null;
  const protocolChanged = update.protocol_version !== CLIENT_PROTOCOL_VERSION;

  return (
    <div
      className="fixed left-1/2 top-3 z-[300] flex max-w-[calc(100vw-24px)] -translate-x-1/2 items-center gap-3 rounded-lg border border-white/10 bg-panel-soft px-4 py-2.5 text-sm text-text-primary shadow-2xl"
      role="status"
    >
      <span>
        {protocolChanged
          ? "호환되지 않는 새 버전이 준비됐습니다."
          : "새 화면 버전이 준비됐습니다."}
      </span>
      <button
        type="button"
        className="inline-flex shrink-0 items-center gap-1.5 rounded-md bg-accent px-3 py-1.5 font-semibold text-white hover:bg-accent-hover"
        onClick={() => window.location.reload()}
      >
        <RefreshCw size={15} aria-hidden="true" />
        새로고침
      </button>
    </div>
  );
}
