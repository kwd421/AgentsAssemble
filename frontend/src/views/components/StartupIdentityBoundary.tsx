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

function startupIdentityRunsOnThisOrigin(): boolean {
  try {
    const url = new URL(window.location.href);
    const hostname = url.hostname.toLowerCase();
    const loopbackHosts = new Set([
      "localhost",
      "127.0.0.1",
      "::1",
      "[::1]",
    ]);
    const nativeShell =
      url.protocol === "tauri:" ||
      url.protocol === "asset:" ||
      hostname === "tauri.localhost";
    const configuredCentral = String(
      import.meta.env.VITE_AGENTSASSEMBLE_CENTRAL_URL || ""
    )
      .trim()
      .replace(/\/+$/, "");
    let centralOrigin = "";
    if (configuredCentral) {
      try {
        centralOrigin = new URL(configuredCentral).origin;
      } catch {
        centralOrigin = "";
      }
    }
    return nativeShell || loopbackHosts.has(hostname) || url.origin === centralOrigin;
  } catch {
    return false;
  }
}

export default function StartupIdentityBoundary({ children }: { children: ReactNode }) {
  const [ready, setReady] = useState(
    () => startupIdentityBypassRequested() || !startupIdentityRunsOnThisOrigin()
  );
  const [deviceToken] = useState(getOrCreateDeviceToken);

  if (ready) return <>{children}</>;
  return <StartupIdentityGate deviceToken={deviceToken} onComplete={() => setReady(true)} />;
}
