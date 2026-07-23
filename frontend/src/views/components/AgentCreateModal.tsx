import { useEffect, useRef, useState } from "react";
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
  catalogRevision?: string;
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
  catalogRevision = "",
  existingSessions = [],
  onClose,
  onCreate,
  onCreated,
}: AgentCreateModalProps) {
  const [providerId, setProviderId] = useState("");
  const [existingSessionId, setExistingSessionId] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [workspacePath, setWorkspacePath] = useState(".");
  const [settings, setSettings] = useState<Record<string, string>>({});
  const [startNow, setStartNow] = useState(false);
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);
  const [deepSeekKey, setDeepSeekKey] = useState("");
  const [credentialStatus, setCredentialStatus] = useState<ProviderCredentialStatus | null>(null);
  const [credentialBusy, setCredentialBusy] = useState(false);
  const wasOpen = useRef(false);
  const selectedProvider = providers.find((provider) => provider.id === providerId);
  const selectedProviderMissing = Boolean(providerId && providers.length && !selectedProvider);
  const reusableSessions = existingSessions.filter(
    (session) =>
      !session.external_owned &&
      session.provider_kind === selectedProvider?.provider_kind &&
      ["stopped", "available"].includes(session.runtime_status) &&
      session.enabled === false &&
      !session.active_turn_id &&
      Boolean(session.runtime_profile_key && session.model && session.permission_mode)
  );
  const invalidControl = existingSessionId || !selectedProvider
    ? undefined
    : selectedProvider.controls.find((control) =>
        !effectiveControlOptions(selectedProvider, control, settings).some(
          (option) => option.value === (settings[control.key] ?? "")
        )
      );
  const canCreate = Boolean(
    meetingId &&
      selectedProvider &&
      (existingSessionId || (catalogRevision && selectedProvider?.startable)) &&
      !invalidControl &&
      displayName.trim() &&
      workspacePath.trim()
  );

  useEffect(() => {
    if (!open) {
      wasOpen.current = false;
      return;
    }
    if (!providers.length) return;
    const current = providers.find((provider) => provider.id === providerId);
    if (!wasOpen.current) {
      if (current) applyProvider(current);
      setStatus("");
      wasOpen.current = true;
      return;
    }
    if (current && !existingSessionId) {
      setSettings((previous) => reconcileProviderSettings(current, previous));
    }
    wasOpen.current = true;
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
    setSettings(initializeProviderSettings(provider));
    setStartNow(provider.startable);
  }

  function applyExistingSession(sessionId: string) {
    setExistingSessionId(sessionId);
    const session = existingSessions.find((item) => item.session_id === sessionId);
    if (!session) {
      if (selectedProvider) {
        setDisplayName(selectedProvider.display_name);
        setSettings(initializeProviderSettings(selectedProvider));
      }
      return;
    }
    setDisplayName(session.display_name);
    setSettings((previous) => ({
      ...previous,
      model: session.model || "",
      reasoning_effort: session.reasoning_effort || "",
      service_tier: session.service_tier || "",
      variant: session.variant || "",
      permission_mode: session.permission_mode || "",
    }));
  }

  async function handleCreate() {
    if (!canCreate || !selectedProvider) {
      setStatus(
        invalidControl
          ? `${invalidControl.label} 선택값을 확인하세요`
          : selectedProvider?.discovery_error || "실행 가능한 provider와 폴더를 확인하세요"
      );
      return;
    }
    setBusy(true);
    setStatus(startNow ? "에이전트 시작 중..." : "에이전트 추가 중...");
    try {
      await onCreate({
        meetingId,
        providerId: selectedProvider.id,
        catalogRevision,
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
              aria-label={provider.display_name}
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
          {selectedProvider && selectedProvider.controls.map((control) => {
            const options = effectiveControlOptions(selectedProvider, control, settings);
            return (
              <ProviderControlField
                key={`${selectedProvider.id}:${control.key}`}
                control={control}
                options={options}
                value={settings[control.key] ?? ""}
                disabled={Boolean(existingSessionId)}
                onChange={(value) =>
                  setSettings((previous) =>
                    reconcileProviderSettings(selectedProvider, {
                      ...previous,
                      [control.key]: value,
                    })
                  )
                }
              />
            );
          })}
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
        {selectedProvider?.discovery_status === "loading" && (
          <p className="dc-agent-create-status">모델 목록을 불러오는 중입니다</p>
        )}
        {selectedProvider?.discovery_status === "failed" && selectedProvider.available && (
          <p className="dc-agent-create-status">
            {selectedProvider.discovery_error || "모델 목록을 불러오지 못했습니다"}
          </p>
        )}
        {selectedProviderMissing && (
          <p className="dc-agent-create-status">선택한 provider가 현재 catalog에 없습니다.</p>
        )}
        {!selectedProvider && !selectedProviderMissing && providers.length > 0 && (
          <p className="dc-agent-create-status">사용할 provider를 선택하세요.</p>
        )}
        {!existingSessionId && invalidControl && (
          <p className="dc-agent-create-status">{invalidControl.label}의 유효한 기본값이 없어 직접 선택해야 합니다.</p>
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
  options,
  value,
  onChange,
  disabled = false,
}: {
  control: ProviderControl;
  options: ProviderControl["options"];
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
        {!options.some((option) => option.value === value) && (
          <option value="" disabled>
            선택 필요
          </option>
        )}
        {options.map((option) => (
          <option key={`${control.key}:${option.value || "default"}`} value={option.value}>
            {control.key === "model" && !equivalentModelNames(option.label, option.value)
              ? `${option.label} · ${option.value}`
              : option.label}
          </option>
        ))}
      </select>
    </label>
  );
}

function equivalentModelNames(label: string, value: string): boolean {
  const normalized = (text: string) =>
    text.normalize("NFKC").toLocaleLowerCase().replace(/[^\p{L}\p{N}]+/gu, "");
  return normalized(label) === normalized(value);
}

function initializeProviderSettings(
  provider: NativeCliProviderAvailability
): Record<string, string> {
  return normalizeProviderSettings(provider, {}, true);
}

function reconcileProviderSettings(
  provider: NativeCliProviderAvailability,
  candidate: Record<string, string>
): Record<string, string> {
  return normalizeProviderSettings(provider, candidate, false);
}

function normalizeProviderSettings(
  provider: NativeCliProviderAvailability,
  candidate: Record<string, string>,
  useDefaults: boolean
): Record<string, string> {
  const next: Record<string, string> = {};
  const modelControl = provider.controls.find((control) => control.key === "model");
  if (modelControl) {
    next.model = validControlValue(modelControl, modelControl.options, candidate.model, useDefaults);
  }
  for (const control of provider.controls) {
    if (control.key === "model") continue;
    const options = effectiveControlOptions(provider, control, { ...candidate, ...next });
    next[control.key] = validControlValue(control, options, candidate[control.key], useDefaults);
  }
  return next;
}

function validControlValue(
  control: ProviderControl,
  options: ProviderControl["options"],
  candidate: string | undefined,
  useDefault: boolean
): string {
  if (candidate !== undefined && options.some((option) => option.value === candidate)) {
    return candidate;
  }
  return useDefault && options.some((option) => option.value === control.default_value)
    ? control.default_value
    : "";
}

function effectiveControlOptions(
  provider: NativeCliProviderAvailability,
  control: ProviderControl,
  settings: Record<string, string>
): ProviderControl["options"] {
  if (!["reasoning_effort", "service_tier"].includes(control.key)) {
    return control.options;
  }
  const modelControl = provider.controls.find((item) => item.key === "model");
  const model = modelControl?.options.find((option) => option.value === settings.model);
  const metadataKey = control.key === "reasoning_effort" ? "reasoning_efforts" : "service_tiers";
  const relation = model?.metadata?.[metadataKey];
  if (!Array.isArray(relation)) return control.options;
  const allowed = new Set(relation.map(String));
  return control.options.filter(
    (option) =>
      allowed.has(option.value) ||
      (control.key === "service_tier" && option.value === "default")
  );
}
