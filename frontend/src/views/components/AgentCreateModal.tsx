import { useEffect, useRef, useState } from "react";
import { Cloud, Folder, Play, Plus, X } from "lucide-react";
import {
  chooseLocalWorkspace,
  deleteProviderCredential,
  fetchProviderCredentialStatus,
  setProviderCredential,
  type FrontendLiveAgentCreateRequest,
  type ProviderCredentialStatus,
} from "../../api";
import type {
  NativeCliProviderAvailability,
  ProviderControl,
} from "../../roomSocketClient";
import type { RoomAgentSession } from "../../api/agentSessions";
import {
  effectiveProviderControlOptions,
  initializeProviderSettings,
  reconcileProviderSettings,
} from "../../lib/providerControlSettings";
import ProviderLogo from "./ProviderLogo";

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
  const [apiPickerOpen, setApiPickerOpen] = useState(false);
  const [providerId, setProviderId] = useState("");
  const [existingSessionId, setExistingSessionId] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [displayNameEdited, setDisplayNameEdited] = useState(false);
  const [workspacePath, setWorkspacePath] = useState("");
  const [workspaceBusy, setWorkspaceBusy] = useState(false);
  const [settings, setSettings] = useState<Record<string, string>>({});
  const [startNow, setStartNow] = useState(false);
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);
  const [providerApiKey, setProviderApiKey] = useState("");
  const [credentialStatus, setCredentialStatus] = useState<ProviderCredentialStatus | null>(null);
  const [credentialBusy, setCredentialBusy] = useState(false);
  const wasOpen = useRef(false);
  const directProviders = providers.filter((provider) => provider.runtime_kind !== "api");
  const apiProviders = providers.filter((provider) => provider.runtime_kind === "api");
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
        !effectiveProviderControlOptions(selectedProvider, control, settings).some(
          (option) => option.value === (settings[control.key] ?? "")
        )
      );
  const canCreate = Boolean(
    meetingId &&
      selectedProvider &&
      (existingSessionId || (catalogRevision && selectedProvider?.startable)) &&
      !invalidControl &&
      displayName.trim() &&
      (existingSessionId || workspacePath.trim())
  );
  const statusMessage = deriveStatusMessage({
    status,
    selectedProvider,
    selectedProviderMissing,
    hasProviders: providers.length > 0,
    invalidControl,
    existingSessionId,
    apiPickerOpen,
  });

  useEffect(() => {
    if (!open) {
      wasOpen.current = false;
      return;
    }
    if (!wasOpen.current) {
      setWorkspacePath("");
      setWorkspaceBusy(false);
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
  }, [open, providers, existingSessionId, providerId]);

  useEffect(() => {
    if (!open || !selectedProvider || existingSessionId || displayNameEdited) return;
    setDisplayName(defaultAgentDisplayName(selectedProvider, settings));
  }, [displayNameEdited, existingSessionId, open, selectedProvider, settings]);

  useEffect(() => {
    if (!open || selectedProvider?.runtime_kind !== "api") {
      setProviderApiKey("");
      setCredentialStatus(null);
      return;
    }
    setCredentialStatus(null);
    fetchProviderCredentialStatus(selectedProvider.id)
      .then(setCredentialStatus)
      .catch((error) => setStatus(error instanceof Error ? error.message : "키 상태 확인 실패"));
  }, [open, selectedProvider?.id, selectedProvider?.runtime_kind]);

  function applyProvider(provider: NativeCliProviderAvailability) {
    const initialSettings = initializeProviderSettings(provider);
    setApiPickerOpen(provider.runtime_kind === "api");
    setProviderId(provider.id);
    setExistingSessionId("");
    setDisplayName(defaultAgentDisplayName(provider, initialSettings));
    setDisplayNameEdited(false);
    setSettings(initialSettings);
    setStartNow(provider.startable);
  }

  function chooseApiCategory() {
    setApiPickerOpen(true);
    setProviderId("");
    setExistingSessionId("");
    setDisplayName("");
    setDisplayNameEdited(false);
    setSettings({});
    setStartNow(false);
    setStatus("");
  }

  function applyExistingSession(sessionId: string) {
    setExistingSessionId(sessionId);
    const session = existingSessions.find((item) => item.session_id === sessionId);
    if (!session) {
      if (selectedProvider) {
        const initialSettings = initializeProviderSettings(selectedProvider);
        setDisplayName(defaultAgentDisplayName(selectedProvider, initialSettings));
        setDisplayNameEdited(false);
        setSettings(initialSettings);
      }
      return;
    }
    setDisplayName(session.display_name);
    setDisplayNameEdited(true);
    setSettings((previous) => ({
      ...previous,
      model: session.model || "",
      reasoning_effort: session.reasoning_effort || "",
      service_tier: session.service_tier || "",
      variant: session.variant || "",
      permission_mode: session.permission_mode || "",
    }));
  }

  function updateProviderControl(key: string, value: string) {
    if (!selectedProvider) return;
    const next = reconcileProviderSettings(
      selectedProvider,
      {
        ...settings,
        [key]: value,
      },
      key
    );
    setSettings(next);
    if (key === "model" && !displayNameEdited) {
      setDisplayName(defaultAgentDisplayName(selectedProvider, next));
    }
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

  async function saveProviderApiKey() {
    if (!selectedProvider || !providerApiKey.trim() || credentialBusy) return;
    setCredentialBusy(true);
    try {
      setCredentialStatus(
        await setProviderCredential(selectedProvider.id, providerApiKey)
      );
      setProviderApiKey("");
      setStatus(`${selectedProvider.display_name} 키가 서버의 보안 저장소에 저장됐습니다`);
    } catch (error) {
      setStatus(
        error instanceof Error
          ? error.message
          : `${selectedProvider.display_name} 키 저장 실패`
      );
    } finally {
      setCredentialBusy(false);
    }
  }

  async function deleteProviderApiKey() {
    if (!selectedProvider || credentialBusy) return;
    setCredentialBusy(true);
    setStatus("");
    try {
      setCredentialStatus(await deleteProviderCredential(selectedProvider.id));
      setProviderApiKey("");
      setStatus(`${selectedProvider.display_name} 저장 키를 삭제했습니다`);
    } catch (error) {
      setStatus(
        error instanceof Error
          ? error.message
          : `${selectedProvider.display_name} 키 삭제 실패`
      );
    } finally {
      setCredentialBusy(false);
    }
  }

  async function pickWorkspace() {
    if (workspaceBusy || existingSessionId) return;
    setWorkspaceBusy(true);
    setStatus("");
    try {
      const selected = await chooseLocalWorkspace();
      if (selected.selected && selected.path) {
        setWorkspacePath(selected.path);
      }
    } catch (error) {
      setStatus(
        error instanceof Error
          ? error.message
          : "작업 폴더를 선택하지 못했습니다"
      );
    } finally {
      setWorkspaceBusy(false);
    }
  }

  function renderProviderChoice(provider: NativeCliProviderAvailability) {
    return (
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
        <ProviderLogo
          providerId={provider.id}
          providerKind={provider.provider_kind}
          size={22}
        />
        <span>{provider.display_name}</span>
      </button>
    );
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

        <div className="dc-agent-create-body">
          <section className="dc-agent-section">
            <p className="dc-agent-section-title">종류</p>
            <div className="dc-agent-provider-grid" role="list" aria-label="에이전트 종류">
              {directProviders.map(renderProviderChoice)}
              {apiProviders.length > 0 && (
                <button
                  type="button"
                  role="listitem"
                  aria-label="API"
                  data-active={apiPickerOpen}
                  onClick={chooseApiCategory}
                >
                  <Cloud size={22} aria-hidden="true" />
                  <span>API</span>
                </button>
              )}
            </div>
          </section>

          {apiPickerOpen && (
            <section className="dc-agent-section">
              <p className="dc-agent-section-title">API 프로바이더</p>
              <div className="dc-agent-provider-grid" role="list" aria-label="API 프로바이더">
                {apiProviders.map(renderProviderChoice)}
              </div>
            </section>
          )}

          <section className="dc-agent-section">
            <p className="dc-agent-section-title">기본 정보</p>
            <div className="dc-agent-field-grid">
              {reusableSessions.length > 0 && (
                <label className="dc-agent-field">
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
              <label className="dc-agent-field">
                <span>표시 이름</span>
                <input
                  value={displayName}
                  placeholder="방에 표시될 이름"
                  disabled={Boolean(existingSessionId)}
                  onChange={(event) => {
                    setDisplayName(event.currentTarget.value);
                    setDisplayNameEdited(true);
                  }}
                />
              </label>
              {!existingSessionId && (
                <label className="dc-agent-field">
                  <span>작업 폴더</span>
                  <div className="dc-agent-folder-field">
                    <Folder size={16} aria-hidden="true" />
                    <input
                      aria-label="선택한 작업 폴더"
                      value={workspacePath}
                      placeholder="선택되지 않음"
                      readOnly
                    />
                    <button
                      type="button"
                      disabled={workspaceBusy}
                      onClick={() => void pickWorkspace()}
                    >
                      {workspaceBusy ? "선택 중..." : "폴더 선택"}
                    </button>
                  </div>
                </label>
              )}
            </div>
          </section>

          {selectedProvider && selectedProvider.controls.length > 0 && (
            <section className="dc-agent-section">
              <p className="dc-agent-section-title">모델 · 실행 설정</p>
              <div className="dc-agent-field-grid dc-agent-field-grid--dual">
                {selectedProvider.controls.map((control) => {
                  const options = effectiveProviderControlOptions(
                    selectedProvider,
                    control,
                    settings
                  );
                  return (
                    <ProviderControlField
                      key={`${selectedProvider.id}:${control.key}`}
                      control={control}
                      options={options}
                      value={settings[control.key] ?? ""}
                      disabled={Boolean(existingSessionId)}
                      onChange={(value) => updateProviderControl(control.key, value)}
                    />
                  );
                })}
              </div>
            </section>
          )}

          {selectedProvider?.runtime_kind === "api" && (
            <section className="dc-agent-section">
              <p className="dc-agent-section-title">인증</p>
              <div className="dc-provider-secret-field">
                <label className="dc-agent-field">
                  <span>API 키</span>
                  <input
                    type="password"
                    autoComplete="off"
                    value={providerApiKey}
                    placeholder={
                      credentialStatus?.configured
                        ? "설정됨"
                        : `${selectedProvider.display_name} API key`
                    }
                    onChange={(event) => setProviderApiKey(event.currentTarget.value)}
                  />
                </label>
                <div>
                  <button
                    type="button"
                    disabled={!providerApiKey.trim() || credentialBusy}
                    onClick={() => void saveProviderApiKey()}
                  >
                    보안 저장
                  </button>
                  {credentialStatus?.source === "keyring" && (
                    <button
                      type="button"
                      disabled={credentialBusy}
                      onClick={() => void deleteProviderApiKey()}
                    >
                      저장 키 삭제
                    </button>
                  )}
                </div>
                <p>{credentialStatus?.configured ? `키 설정됨 · ${credentialStatus.source}` : "키 없음"}</p>
              </div>
            </section>
          )}

          {statusMessage && (
            <p className="dc-agent-create-status preserve-words">{statusMessage}</p>
          )}
        </div>

        <footer className="dc-agent-create-footer">
          <button
            type="button"
            className="dc-agent-launch-toggle"
            role="switch"
            aria-checked={startNow}
            aria-label="추가하자마자 실행"
            data-on={startNow}
            disabled={!selectedProvider?.startable}
            onClick={() => setStartNow((value) => !value)}
          >
            <span className="dc-agent-launch-switch" aria-hidden="true">
              <i />
            </span>
            <span className="dc-agent-launch-text">
              <strong>{startNow ? "추가하고 바로 실행" : "목록에만 추가"}</strong>
              <em>
                {startNow
                  ? "추가와 동시에 세션이 켜집니다."
                  : "카드에서 언제든 켤 수 있어요."}
              </em>
            </span>
          </button>
          <div className="dc-agent-footer-actions">
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
              {busy ? "처리 중..." : startNow ? "추가하고 실행" : "추가"}
            </button>
          </div>
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
    <label className="dc-agent-field">
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
            {option.label}
          </option>
        ))}
      </select>
    </label>
  );
}

function deriveStatusMessage({
  status,
  selectedProvider,
  selectedProviderMissing,
  hasProviders,
  invalidControl,
  existingSessionId,
  apiPickerOpen,
}: {
  status: string;
  selectedProvider: NativeCliProviderAvailability | undefined;
  selectedProviderMissing: boolean;
  hasProviders: boolean;
  invalidControl: ProviderControl | undefined;
  existingSessionId: string;
  apiPickerOpen: boolean;
}): string {
  if (status) return status;
  if (selectedProvider && !selectedProvider.available) {
    return selectedProvider.discovery_error || "CLI를 찾지 못했습니다";
  }
  if (selectedProvider?.discovery_status === "loading") {
    return "모델 목록을 불러오는 중입니다";
  }
  if (selectedProvider?.discovery_status === "failed" && selectedProvider.available) {
    return selectedProvider.discovery_error || "모델 목록을 불러오지 못했습니다";
  }
  if (selectedProviderMissing) {
    return "선택한 provider가 현재 catalog에 없습니다.";
  }
  if (!selectedProvider && !selectedProviderMissing && hasProviders) {
    if (apiPickerOpen) {
      return "사용할 API 프로바이더를 선택하세요.";
    }
    return "사용할 provider를 선택하세요.";
  }
  if (!existingSessionId && invalidControl) {
    return `${invalidControl.label}의 유효한 기본값이 없어 직접 선택해야 합니다.`;
  }
  return "";
}

function defaultAgentDisplayName(
  provider: NativeCliProviderAvailability,
  settings: Record<string, string>
): string {
  const providerName = provider.display_name.trim();
  const modelControl = provider.controls.find((control) => control.key === "model");
  const modelOption = modelControl?.options.find((option) => option.value === settings.model);
  const modelName = String(modelOption?.label || "").trim();
  if (!modelName) return providerName;

  const providerToken = providerName.toLocaleLowerCase();
  const modelToken = modelName.toLocaleLowerCase();
  if (
    modelToken === providerToken ||
    modelToken.startsWith(`${providerToken} `) ||
    modelToken.startsWith(`${providerToken}-`)
  ) {
    return modelName;
  }
  return `${providerName} ${modelName}`;
}
