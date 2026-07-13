import { useEffect, useState } from "react";
import { Bot, Folder, Play, Plus, X } from "lucide-react";
import {
  deleteDeepSeekCredential,
  fetchDeepSeekCredentialStatus,
  setDeepSeekCredential,
  type FrontendLiveAgentCreateRequest,
  type ProviderCredentialStatus,
} from "../../api";
import type {
  NativeCliProviderAvailability,
  ProviderControl,
} from "../../roomSocketClient";
import type { RoomAgentSession } from "../../api/agentSessions";

type AgentCreateModalProps = {
  open: boolean;
  meetingId: string;
  roomLabel: string;
  providers: NativeCliProviderAvailability[];
  existingSessions?: RoomAgentSession[];
  onClose: () => void;
  onCreate: (request: FrontendLiveAgentCreateRequest) => Promise<void>;
  onCreated?: () => void;
};

export default function AgentCreateModal({
  open,
  meetingId,
  roomLabel,
  providers,
  existingSessions = [],
  onClose,
  onCreate,
  onCreated,
}: AgentCreateModalProps) {
  const [providerId, setProviderId] = useState("codex");
  const [existingSessionId, setExistingSessionId] = useState("");
  const [displayName, setDisplayName] = useState("Codex");
  const [workspacePath, setWorkspacePath] = useState(".");
  const [settings, setSettings] = useState<Record<string, string>>({});
  const [startNow, setStartNow] = useState(true);
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);
  const [deepSeekKey, setDeepSeekKey] = useState("");
  const [credentialStatus, setCredentialStatus] = useState<ProviderCredentialStatus | null>(null);
  const [credentialBusy, setCredentialBusy] = useState(false);
  const selectedProvider =
    providers.find((provider) => provider.id === providerId) || providers[0];
  const reusableSessions = existingSessions.filter(
    (session) =>
      !session.external_owned &&
      session.provider_kind === selectedProvider?.provider_kind &&
      !["starting", "idle", "busy", "paused", "recovering", "stopping"].includes(
        session.runtime_status
      )
  );
  const canCreate = Boolean(
    meetingId && selectedProvider?.startable && displayName.trim() && workspacePath.trim()
  );

  useEffect(() => {
    if (!open || !providers.length) return;
    const current = providers.find((provider) => provider.id === providerId) || providers[0];
    applyProvider(current);
    setStatus("");
  }, [open, providers]);

  useEffect(() => {
    if (!open || selectedProvider?.id !== "deepseek") return;
    fetchDeepSeekCredentialStatus()
      .then(setCredentialStatus)
      .catch((error) => setStatus(error instanceof Error ? error.message : "키 상태 확인 실패"));
  }, [open, selectedProvider?.id]);

  function applyProvider(provider: NativeCliProviderAvailability) {
    setProviderId(provider.id);
    setExistingSessionId("");
    setDisplayName(provider.display_name);
    setSettings(
      Object.fromEntries(
        (provider.controls || []).map((control) => [
          control.key,
          control.default_value || control.options?.[0]?.value || "",
        ])
      )
    );
    setStartNow(provider.startable);
  }

  function applyExistingSession(sessionId: string) {
    setExistingSessionId(sessionId);
    const session = existingSessions.find((item) => item.session_id === sessionId);
    if (!session) return;
    setDisplayName(session.display_name);
    setSettings((previous) => ({
      ...previous,
      model: session.model || previous.model || "",
      reasoning_effort: session.reasoning_effort || previous.reasoning_effort || "",
      service_tier: session.service_tier || previous.service_tier || "",
      variant: session.variant || previous.variant || "",
      permission_mode: session.permission_mode || previous.permission_mode || "meeting_read_only",
    }));
  }

  async function handleCreate() {
    if (!canCreate || !selectedProvider) {
      setStatus(selectedProvider?.discovery_error || "실행 가능한 provider와 폴더를 확인하세요");
      return;
    }
    setBusy(true);
    setStatus(startNow ? "에이전트 시작 중..." : "에이전트 추가 중...");
    try {
      await onCreate({
        meetingId,
        providerId: selectedProvider.id,
        sessionId: existingSessionId || undefined,
        displayName,
        workspacePath,
        modelId: settings.model || "",
        reasoningEffort: settings.reasoning_effort || "",
        serviceTier: settings.service_tier || "",
        variant: settings.variant || "",
        permissionMode: settings.permission_mode || "meeting_read_only",
        startNow,
      });
      onCreated?.();
      onClose();
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "에이전트 추가 실패");
    } finally {
      setBusy(false);
    }
  }

  async function saveDeepSeekKey() {
    if (!deepSeekKey.trim() || credentialBusy) return;
    setCredentialBusy(true);
    try {
      setCredentialStatus(await setDeepSeekCredential(deepSeekKey));
      setDeepSeekKey("");
      setStatus("DeepSeek 키가 서버의 보안 저장소에 저장됐습니다");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "DeepSeek 키 저장 실패");
    } finally {
      setCredentialBusy(false);
    }
  }

  if (!open) return null;

  return (
    <div className="dc-modal-backdrop" role="presentation" onMouseDown={onClose}>
      <section
        className="dc-agent-create-modal"
        role="dialog"
        aria-modal="true"
        aria-label="에이전트 추가"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="dc-agent-create-head">
          <div>
            <p className="dc-agent-create-kicker preserve-words">{roomLabel}</p>
            <h2>에이전트 추가</h2>
          </div>
          <button type="button" onClick={onClose} aria-label="닫기">
            <X size={18} />
          </button>
        </header>

        <div className="dc-agent-provider-grid" role="list" aria-label="에이전트 종류">
          {providers.map((provider) => (
            <button
              key={provider.id}
              type="button"
              role="listitem"
              data-active={provider.id === selectedProvider?.id}
              disabled={!provider.available}
              onClick={() => {
                applyProvider(provider);
                setStatus(provider.discovery_error || "");
              }}
            >
              <Bot size={16} />
              <span>{provider.display_name}</span>
            </button>
          ))}
        </div>

        <div className="dc-agent-create-fields">
          {reusableSessions.length > 0 && (
            <label>
              <span>기존 세션</span>
              <select
                aria-label="기존 세션"
                value={existingSessionId}
                onChange={(event) => applyExistingSession(event.currentTarget.value)}
              >
                <option value="">새 세션 만들기</option>
                {reusableSessions.map((session) => (
                  <option key={session.session_id} value={session.session_id}>
                    {session.display_name} · {session.model || session.provider_kind}
                  </option>
                ))}
              </select>
            </label>
          )}
          <label>
            <span>이름</span>
            <input
              value={displayName}
              disabled={Boolean(existingSessionId)}
              onChange={(event) => setDisplayName(event.currentTarget.value)}
            />
          </label>
          <label>
            <span>폴더</span>
            <div className="dc-agent-folder-field">
              <Folder size={16} />
              <input
                value={workspacePath}
                disabled={Boolean(existingSessionId)}
                onChange={(event) => setWorkspacePath(event.currentTarget.value)}
                placeholder="."
              />
            </div>
          </label>
          {(selectedProvider?.controls || []).map((control) => (
            <ProviderControlField
              key={`${selectedProvider.id}:${control.key}`}
              control={control}
              value={settings[control.key] || ""}
              disabled={Boolean(existingSessionId)}
              onChange={(value) => setSettings((previous) => ({ ...previous, [control.key]: value }))}
            />
          ))}
          {selectedProvider?.id === "deepseek" && (
            <div className="dc-provider-secret-field">
              <label>
                <span>API 키</span>
                <input
                  type="password"
                  autoComplete="off"
                  value={deepSeekKey}
                  placeholder={credentialStatus?.configured ? "설정됨" : "DeepSeek API key"}
                  onChange={(event) => setDeepSeekKey(event.currentTarget.value)}
                />
              </label>
              <div>
                <button type="button" disabled={!deepSeekKey.trim() || credentialBusy} onClick={() => void saveDeepSeekKey()}>
                  보안 저장
                </button>
                {credentialStatus?.source === "keyring" && (
                  <button
                    type="button"
                    disabled={credentialBusy}
                    onClick={() => {
                      setCredentialBusy(true);
                      void deleteDeepSeekCredential()
                        .then((next) => {
                          setCredentialStatus(next);
                          setDeepSeekKey("");
                        })
                        .finally(() => setCredentialBusy(false));
                    }}
                  >
                    저장 키 삭제
                  </button>
                )}
              </div>
              <p>{credentialStatus?.configured ? `키 설정됨 · ${credentialStatus.source}` : "키 없음"}</p>
            </div>
          )}
          <label className="dc-agent-create-toggle">
            <input
              type="checkbox"
              checked={startNow}
              disabled={!selectedProvider?.startable}
              onChange={(event) => setStartNow(event.currentTarget.checked)}
            />
            <span>추가 후 바로 시작</span>
          </label>
        </div>

        {selectedProvider && !selectedProvider.available && (
          <p className="dc-agent-create-status">{selectedProvider.discovery_error || "CLI를 찾지 못했습니다"}</p>
        )}
        {status && <p className="dc-agent-create-status preserve-words">{status}</p>}

        <footer className="dc-agent-create-footer">
          <button type="button" className="dc-agent-create-secondary" onClick={onClose}>
            취소
          </button>
          <button
            type="button"
            className="dc-agent-create-primary"
            disabled={!canCreate || busy}
            onClick={() => void handleCreate()}
          >
            {startNow ? <Play size={16} /> : <Plus size={16} />}
            {startNow ? "추가하고 시작" : "추가"}
          </button>
        </footer>
      </section>
    </div>
  );
}

function ProviderControlField({
  control,
  value,
  onChange,
  disabled = false,
}: {
  control: ProviderControl;
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
}) {
  return (
    <label>
      <span>{control.label}</span>
      <select
        aria-label={control.label}
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(event.currentTarget.value)}
      >
        {(control.options || []).map((option) => (
          <option key={`${control.key}:${option.value || "default"}`} value={option.value}>
            {control.key === "model" && option.label !== option.value
              ? `${option.label} · ${option.value}`
              : option.label}
          </option>
        ))}
      </select>
    </label>
  );
}
