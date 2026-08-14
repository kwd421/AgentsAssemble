type TauriInternals = {
  invoke<T>(command: string, args?: Record<string, unknown>): Promise<T>;
};

function tauriInternals(): TauriInternals | undefined {
  return (
    window as typeof window & {
      __TAURI_INTERNALS__?: TauriInternals;
    }
  ).__TAURI_INTERNALS__;
}

export function isDesktopWebview(): boolean {
  return Boolean(tauriInternals());
}

export async function openDesktopGoogleLogin(url: string): Promise<void> {
  const tauri = tauriInternals();
  if (!tauri) {
    throw new Error("데스크톱 브라우저 연결 기능을 사용할 수 없습니다.");
  }
  await tauri.invoke("open_google_account_login", { url });
}

export async function openDesktopCentralGoogleLogin(url: string): Promise<void> {
  const tauri = tauriInternals();
  if (!tauri) {
    throw new Error("데스크톱 중앙 로그인 기능을 사용할 수 없습니다.");
  }
  await tauri.invoke("open_central_google_login", { url });
}

export async function cacheNativeRoomDirectory(rooms: unknown[]): Promise<void> {
  const tauri = tauriInternals();
  if (!tauri) return;
  await tauri.invoke("cache_selected_room_directory", {
    rooms: JSON.stringify(rooms),
  });
}
