import { useState, type ReactNode } from "react";

import { getOrCreateDeviceToken } from "../../lib/deviceIdentity";
import StartupIdentityGate from "./StartupIdentityGate";

function startupIdentityBypassRequested(): boolean {
  try {
    const url = new URL(window.location.href);
    const query = url.searchParams;
    const fragment = new URLSearchParams(url.hash.replace(/^#/, ""));
    return Boolean(
      query.get("guest") === "1" ||
        query.has("invite") ||
        query.get("recover") === "1" ||
        query.has("pair") ||
        fragment.has("invite") ||
        fragment.has("recovery") ||
        fragment.has("pairing") ||
        fragment.has("operatorPairing")
    );
  } catch {
    return false;
  }
}

export default function StartupIdentityBoundary({ children }: { children: ReactNode }) {
  const [ready, setReady] = useState(startupIdentityBypassRequested);
  const [deviceToken] = useState(getOrCreateDeviceToken);

  if (ready) return <>{children}</>;
  return <StartupIdentityGate deviceToken={deviceToken} onComplete={() => setReady(true)} />;
}
