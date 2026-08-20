import { useEffect, useRef, useState } from "react";
import {
  ArrowLeft,
  ArrowRight,
  Check,
  Copy,
  KeyRound,
  LoaderCircle,
  LogIn,
  UserRound,
} from "lucide-react";

import { fetchAccountStatus } from "../../api/identity";
import { saveUserProfile } from "../../api/room";
import {
  bootstrapCentral,
  centralIdentityConfigured,
  clearPendingCentralRecoveryCode,
  createCentralGuest,
  isCentralAuthenticationError,
  loadCentralSession,
  loadPendingCentralRecoveryCode,
  loginCentralGoogle,
  recoverCentralGuest,
  registerLocalServer,
} from "../../lib/centralIdentity";
import {
  rememberGuestProfile,
  rememberStartupIdentitySelection,
} from "../../lib/deviceIdentity";
import {
  hydratePersistedRoom,
  mergeServerRoomsIntoDock,
  persistableRoom,
  type ServerRoomDockSource,
} from "../../lib/roomDockModel";
import {
  loadRoomDockItems,
  persistRoomDockItems,
} from "../../lib/roomDockPersistence";
import { DEFAULT_USER_PROFILE } from "../../lib/userProfileModel";
import GoogleAccountSettings from "./GoogleAccountSettings";

type Screen = "choice" | "guest" | "recover" | "recovery-code";

async function saveLocalProfile(displayName: string, deviceToken: string) {
  const name = displayName.trim();
  if (!name) return;
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
    // Central identity and local profile remain independently recoverable.
  }
}

export default function StartupIdentityGate({
  deviceToken,
  onComplete,
}: {
  deviceToken: string;
  onComplete: () => void;
}) {
  const centralEnabled = centralIdentityConfigured();
  const [screen, setScreen] = useState<Screen>("choice");
  const [displayName, setDisplayName] = useState("");
  const [recoveryInput, setRecoveryInput] = useState("");
  const [issuedRecoveryCode, setIssuedRecoveryCode] = useState("");
  const [savedRecoveryCode, setSavedRecoveryCode] = useState(false);
  const [copied, setCopied] = useState(false);
  const [checking, setChecking] = useState(true);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("저장된 사용자 확인 중");
  const [error, setError] = useState("");
  const googleAbortController = useRef<AbortController | null>(null);

  useEffect(
    () => () => {
      googleAbortController.current?.abort();
    },
    []
  );

  async function enterApplication() {
    setChecking(true);
    setStatus("로컬 엔진과 방 목록을 준비하는 중");
    try {
      const response = await fetch("/api/rooms", { cache: "no-store" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const payload = (await response.json()) as {
        rooms?: ServerRoomDockSource[];
        server_id?: string;
      };
      const current = loadRoomDockItems().map(hydratePersistedRoom);
      const synchronized = mergeServerRoomsIntoDock(
        current,
        payload.rooms || [],
        window.location.origin,
        String(payload.server_id || "")
      );
      persistRoomDockItems(synchronized.map(persistableRoom));
    } catch {
      // The cached/local-first application still opens when synchronization fails.
    }
    rememberStartupIdentitySelection();
    onComplete();
  }

  async function continueAfterRecoveryCode() {
    if (!savedRecoveryCode || busy) return;
    setBusy(true);
    clearPendingCentralRecoveryCode();
    await registerLocalServer(deviceToken).catch(() => undefined);
    await enterApplication();
  }

  useEffect(() => {
    let active = true;
    async function initialize() {
      if (centralEnabled) {
        const pendingRecoveryCode = loadPendingCentralRecoveryCode();
        if (pendingRecoveryCode) {
          if (active) {
            setIssuedRecoveryCode(pendingRecoveryCode);
            setSavedRecoveryCode(false);
            setCopied(false);
            setScreen("recovery-code");
            setChecking(false);
          }
          return;
        }

        const existing = loadCentralSession();
        if (!existing) {
          if (active) setChecking(false);
          return;
        }
        try {
          setStatus("중앙 신원과 서버 목록을 확인하는 중");
          await bootstrapCentral();
          await registerLocalServer(deviceToken).catch(() => undefined);
        } catch (reason) {
          if (isCentralAuthenticationError(reason)) {
            if (active) {
              setError("중앙 로그인이 만료됐습니다. 다시 로그인해 주세요.");
              setChecking(false);
            }
            return;
          }
          // Once a device has a valid remembered identity, central downtime must not prevent local startup.
        }
        if (active) await enterApplication();
        return;
      }
      fetchAccountStatus({ deviceToken })
        .then((account) => {
          if (!active) return;
          if (account.account) {
            rememberStartupIdentitySelection();
            onComplete();
          } else {
            setChecking(false);
          }
        })
        .catch(() => {
          if (active) setChecking(false);
        });
    }
    void initialize();
    return () => {
      active = false;
    };
  }, [centralEnabled, deviceToken, onComplete]);

  async function createGuest() {
    const name = displayName.trim();
    if (!name || busy) return;
    setBusy(true);
    setError("");
    setStatus("복구 가능한 게스트 신원을 만드는 중");
    try {
      const result = await createCentralGuest(name);
      await saveLocalProfile(result.person.display_name || name, deviceToken);
      await registerLocalServer(deviceToken).catch(() => undefined);
      setIssuedRecoveryCode(result.recovery_code);
      setSavedRecoveryCode(false);
      setCopied(false);
      setScreen("recovery-code");
    } catch (reason) {
      const pending = loadPendingCentralRecoveryCode();
      if (pending) {
        setIssuedRecoveryCode(pending);
        setSavedRecoveryCode(false);
        setCopied(false);
        setScreen("recovery-code");
      } else {
        setError(
          reason instanceof Error
            ? reason.message
            : "게스트 신원을 만들지 못했습니다."
        );
      }
    } finally {
      setBusy(false);
    }
  }

  async function recoverGuest() {
    if (!recoveryInput.trim() || busy) return;
    setBusy(true);
    setError("");
    setStatus("게스트 신원을 복구하고 이전 코드를 폐기하는 중");
    try {
      const result = await recoverCentralGuest(recoveryInput);
      await saveLocalProfile(result.person.display_name || "Guest", deviceToken);
      await registerLocalServer(deviceToken).catch(() => undefined);
      setIssuedRecoveryCode(result.recovery_code);
      setSavedRecoveryCode(false);
      setCopied(false);
      setScreen("recovery-code");
    } catch (reason) {
      const pending = loadPendingCentralRecoveryCode();
      if (pending) {
        setIssuedRecoveryCode(pending);
        setSavedRecoveryCode(false);
        setCopied(false);
        setScreen("recovery-code");
      } else {
        setError(
          reason instanceof Error
            ? reason.message
            : "게스트 신원을 복구하지 못했습니다."
        );
      }
    } finally {
      setBusy(false);
    }
  }

  async function googleLogin() {
    if (busy) return;
    const controller = new AbortController();
    googleAbortController.current = controller;
    setBusy(true);
    setError("");
    try {
      await loginCentralGoogle(setStatus, controller.signal);
      await registerLocalServer(deviceToken).catch(() => undefined);
      await enterApplication();
    } catch (reason) {
      setChecking(false);
      setError(
        typeof reason === "object" &&
          reason !== null &&
          "name" in reason &&
          reason.name === "AbortError"
          ? "Google 로그인을 취소했습니다."
          : reason instanceof Error
          ? reason.message
          : "Google 로그인을 완료하지 못했습니다."
      );
    } finally {
      if (googleAbortController.current === controller) {
        googleAbortController.current = null;
      }
      setBusy(false);
    }
  }

  async function copyRecoveryCode() {
    try {
      await navigator.clipboard.writeText(issuedRecoveryCode);
      setCopied(true);
    } catch {
      setError("복사 권한이 거부됐습니다. 코드를 직접 선택해 복사해 주세요.");
    }
  }

  async function continueLegacyGuest() {
    const name = displayName.trim();
    if (!name || busy) return;
    setBusy(true);
    await saveLocalProfile(name, deviceToken);
    rememberStartupIdentitySelection();
    onComplete();
  }

  if (checking) {
    return (
      <div className="fixed inset-0 z-[400] grid place-items-center bg-[#101114] p-5">
        <main className="grid place-items-center gap-3" aria-label="앱 시작 준비">
          <LoaderCircle size={28} className="animate-spin text-[#8d96ff]" />
          <p role="status" className="text-[12px] font-bold text-text-muted">
            {status}
          </p>
        </main>
      </div>
    );
  }

  if (!centralEnabled) {
    return (
      <div className="fixed inset-0 z-[400] grid place-items-center overflow-y-auto bg-[#101114] p-5">
        <main className="grid w-full max-w-[520px] gap-5 rounded-xl border border-white/10 bg-[#202126] p-6 shadow-2xl">
          <header className="grid gap-2">
            <span className="text-[11px] font-black uppercase tracking-[0.16em] text-[#8d96ff]">
              AgentsAssemble
            </span>
            <h1 className="text-2xl font-black text-text-primary">어떻게 사용할까요?</h1>
            <p className="text-[13px] font-semibold leading-5 text-text-muted">
              중앙 디렉터리가 설정되지 않아 기존 로컬 신원 모드로 시작합니다.
            </p>
          </header>
          <GoogleAccountSettings
            identity={{ deviceToken }}
            onAccountConnected={() => {
              rememberStartupIdentitySelection();
              onComplete();
            }}
          />
          <div className="flex items-center gap-3 text-[10px] font-black uppercase tracking-wider text-text-muted">
            <span className="h-px flex-1 bg-white/10" /> 또는{" "}
            <span className="h-px flex-1 bg-white/10" />
          </div>
          <label className="grid gap-2 text-[11px] font-black text-text-secondary">
            게스트 표시 이름
            <input
              autoFocus
              maxLength={80}
              value={displayName}
              className="min-h-10 rounded-md bg-[#2b2d31] px-3 text-[13px] text-text-primary outline-none"
              onChange={(event) => setDisplayName(event.currentTarget.value)}
            />
          </label>
          <button
            type="button"
            className="min-h-10 rounded-md bg-[#5865f2] px-4 text-[13px] font-black text-white disabled:opacity-50"
            disabled={!displayName.trim() || busy}
            onClick={() => void continueLegacyGuest()}
          >
            게스트로 계속
          </button>
        </main>
      </div>
    );
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
          <h1 className="text-2xl font-black text-text-primary">
            {screen === "recovery-code" ? "복구 코드를 보관하세요" : "먼저 로그인해 주세요"}
          </h1>
          <p className="text-[13px] font-semibold leading-5 text-text-muted">
            {screen === "recovery-code"
              ? "이 코드는 다른 기기에서 같은 게스트 신원과 서버 목록을 복구할 때 필요합니다. 중앙에는 코드 원문을 저장하지 않습니다."
              : "Google 계정은 내 서버 목록을 기기 간 동기화할 때만 사용합니다. 방과 메시지는 각 서버에 그대로 남습니다."}
          </p>
        </header>

        {screen === "choice" && (
          <div className="grid gap-3">
            <button
              type="button"
              className="flex min-h-12 items-center justify-center gap-2 rounded-md bg-[#5865f2] px-4 text-[14px] font-black text-white disabled:opacity-60"
              disabled={busy}
              onClick={() => void googleLogin()}
            >
              {busy ? (
                <LoaderCircle size={17} className="animate-spin" />
              ) : (
                <LogIn size={17} />
              )}{" "}
              Google로 계속
            </button>
            {busy && (
              <button
                type="button"
                className="min-h-10 rounded-md border border-white/10 px-4 text-[12px] font-black text-text-primary"
                onClick={() => googleAbortController.current?.abort()}
              >
                Google 로그인 취소
              </button>
            )}
            <button
              type="button"
              className="flex min-h-12 items-center justify-center gap-2 rounded-md bg-[#2b2d31] px-4 text-[14px] font-black text-text-primary"
              onClick={() => {
                setError("");
                setScreen("guest");
              }}
            >
              <UserRound size={17} /> 새 게스트로 계속
            </button>
            <button
              type="button"
              className="min-h-10 text-[12px] font-black text-[#aeb4ff]"
              onClick={() => {
                setError("");
                setScreen("recover");
              }}
            >
              이미 복구 코드가 있습니다
            </button>
          </div>
        )}

        {screen === "guest" && (
          <section className="grid gap-3 rounded-lg bg-[#1b1c20] p-4">
            <button
              type="button"
              className="flex w-fit items-center gap-1 text-[11px] font-black text-text-muted"
              onClick={() => setScreen("choice")}
            >
              <ArrowLeft size={14} /> 뒤로
            </button>
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
                  if (event.key === "Enter") void createGuest();
                }}
              />
            </label>
            <button
              type="button"
              className="flex min-h-10 items-center justify-center gap-2 rounded-md bg-[#5865f2] px-4 text-[13px] font-black text-white disabled:opacity-50"
              disabled={!displayName.trim() || busy}
              onClick={() => void createGuest()}
            >
              {busy ? (
                <LoaderCircle size={16} className="animate-spin" />
              ) : (
                <ArrowRight size={16} />
              )}{" "}
              {busy ? "만드는 중…" : "게스트 만들기"}
            </button>
          </section>
        )}

        {screen === "recover" && (
          <section className="grid gap-3 rounded-lg bg-[#1b1c20] p-4">
            <button
              type="button"
              className="flex w-fit items-center gap-1 text-[11px] font-black text-text-muted"
              onClick={() => setScreen("choice")}
            >
              <ArrowLeft size={14} /> 뒤로
            </button>
            <label className="grid gap-1.5 text-[11px] font-black text-text-secondary">
              게스트 복구 코드
              <input
                autoFocus
                autoComplete="one-time-code"
                spellCheck={false}
                value={recoveryInput}
                placeholder="XXXX-XXXX-…"
                className="min-h-10 rounded-md bg-[#2b2d31] px-3 font-mono text-[13px] text-text-primary outline-none"
                onChange={(event) =>
                  setRecoveryInput(event.currentTarget.value.toUpperCase())
                }
              />
            </label>
            <button
              type="button"
              className="flex min-h-10 items-center justify-center gap-2 rounded-md bg-[#5865f2] px-4 text-[13px] font-black text-white disabled:opacity-50"
              disabled={!recoveryInput.trim() || busy}
              onClick={() => void recoverGuest()}
            >
              {busy ? (
                <LoaderCircle size={16} className="animate-spin" />
              ) : (
                <KeyRound size={16} />
              )}{" "}
              {busy ? "복구 중…" : "같은 게스트로 로그인"}
            </button>
          </section>
        )}

        {screen === "recovery-code" && (
          <section className="grid gap-4 rounded-lg bg-[#1b1c20] p-4">
            <label className="grid gap-2 text-[11px] font-black text-text-secondary">
              새 복구 코드
              <input
                readOnly
                value={issuedRecoveryCode}
                onFocus={(event) => event.currentTarget.select()}
                className="min-h-12 rounded-md bg-[#2b2d31] px-3 font-mono text-[13px] font-black tracking-wide text-text-primary outline-none"
              />
            </label>
            <button
              type="button"
              className="flex min-h-10 items-center justify-center gap-2 rounded-md bg-[#3a3c42] px-4 text-[12px] font-black text-text-primary"
              onClick={() => void copyRecoveryCode()}
            >
              {copied ? <Check size={16} /> : <Copy size={16} />} {copied ? "복사됨" : "복구 코드 복사"}
            </button>
            <p className="rounded-md bg-[#3a2526] p-3 text-[11px] font-bold leading-5 text-[#ffb4b5]">
              이 코드를 잃으면 다른 기기에서 이 게스트 신원을 복구할 수 없습니다. 비밀번호 관리자나 안전한 오프라인 장소에 보관하세요. 복구 직후에는 이전 코드가 폐기됩니다.
            </p>
            <label className="flex items-start gap-2 text-[12px] font-bold leading-5 text-text-secondary">
              <input
                type="checkbox"
                className="mt-1"
                checked={savedRecoveryCode}
                onChange={(event) => setSavedRecoveryCode(event.currentTarget.checked)}
              />
              복구 코드를 안전한 곳에 저장했습니다.
            </label>
            <button
              type="button"
              className="flex min-h-10 items-center justify-center gap-2 rounded-md bg-[#5865f2] px-4 text-[13px] font-black text-white disabled:opacity-50"
              disabled={!savedRecoveryCode || busy}
              onClick={() => void continueAfterRecoveryCode()}
            >
              {busy ? <LoaderCircle size={16} className="animate-spin" /> : <ArrowRight size={16} />} 계속
            </button>
          </section>
        )}

        {busy && screen === "choice" && (
          <p role="status" className="text-center text-[11px] font-bold text-text-muted">
            {status}
          </p>
        )}
        {error && (
          <p
            role="alert"
            className="rounded-md bg-[#3a2526] p-3 text-[11px] font-bold leading-5 text-[#ffb4b5]"
          >
            {error}
          </p>
        )}
      </main>
    </div>
  );
}
