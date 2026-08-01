import { useEffect, useRef, useState } from "react";
import { Folder, Play, Plus, X } from "lucide-react";
import {
  chooseLocalWorkspace,
  deleteProviderCredential,
  fetchProviderCredentialStatus,
  refreshProviderCatalog,
  setProviderCredential,
  startFrontendLiveAgentLogin,
  type FrontendLiveAgentCreateRequest,
  type ProviderCredentialStatus,
} from "../../api";
import type { NativeCliProviderAvailability, ProviderControl } from "../../roomSocketClient";
import type { RoomAgentSession } from "../../api/agentSessions";
import {
  displayProviderControls,
  effectiveProviderControlOptions,
  initializeProviderSettings,
  reconcileProviderSettings,
} from "../../lib/providerControlSettings";
import {
  PROVIDER_GROUPS,
  projectProvidersByCatalogGroup,
  providerCatalogGroup,
  providerGroupLabel,
  type ProviderCatalogGroup,
} from "../../lib/providerCatalogGroups";
import ProviderLogo from "./ProviderLogo";
import ProviderControlSelect from "./ProviderControlSelect";
import ProviderControlToggle from "./ProviderControlToggle";

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
  const [providerGroup, setProviderGroup] = useState<ProviderCatalogGroup | "">(
    "subscription"
  );
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
  const [customEndpoint, setCustomEndpoint] = useState("");
  const [customModel, setCustomModel] = useState("");
  const [credentialStatus, setCredentialStatus] = useState<ProviderCredentialStatus | null>(null);
  const [credentialBusy, setCredentialBusy] = useState(false);
  const [loginBusy, setLoginBusy] = useState(false);
  const wasOpen = useRef(false);
  const groupedProviders = projectProvidersByCatalogGroup(providers);
  const visibleProviders = providerGroup ? groupedProviders[providerGroup] : [];
  const selectedProvider = visibleProviders.find((provider) => provider.id === providerId);
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
      (!selectedProvider.custom_endpoint || customEndpoint.trim()) &&
      (!selectedProvider.custom_model || customModel.trim()) &&
      (
        existingSessionId ||
        selectedProvider?.workspace_required === false ||
        workspacePath.trim()
      )
  );
  const statusMessage = deriveStatusMessage({
    status,
    workspacePath,
    selectedProvider,
    selectedProviderMissing,
    hasProviders: providers.length > 0,
    invalidControl,
    existingSessionId,
  });

  useEffect(() => {
    if (!open) {
      wasOpen.current = false;
      setCustomEndpoint("");
      setCustomModel("");
      return;
    }
    if (!wasOpen.current) {
      setWorkspacePath("");
      setWorkspaceBusy(false);
    }
    if (!providers.length) return;
    const current = visibleProviders.find((provider) => provider.id === providerId);
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
  }, [open, providers, existingSessionId, providerGroup, providerId]);

  useEffect(() => {
    if (!open || !selectedProvider || existingSessionId || displayNameEdited) return;
    setDisplayName(defaultAgentDisplayName(selectedProvider, settings));
  }, [displayNameEdited, existingSessionId, open, selectedProvider, settings]);

  useEffect(() => {
    if (!open || !selectedProvider || providerCatalogGroup(selectedProvider) !== "api") {
      setProviderApiKey("");
      setCredentialStatus(null);
      return;
    }
    setCredentialStatus(null);
    fetchProviderCredentialStatus(selectedProvider.id)
      .then(setCredentialStatus)
      .catch((error) => setStatus(error instanceof Error ? error.message : "키 상태 확인 실패"));
  }, [open, selectedProvider?.id, selectedProvider?.catalog_group, selectedProvider?.runtime_kind]);

  function applyProvider(provider: NativeCliProviderAvailability) {
    const initialSettings = initializeProviderSettings(provider);
    setProviderGroup(providerCatalogGroup(provider));
    setProviderId(provider.id);
    setExistingSessionId("");
    setDisplayName(defaultAgentDisplayName(provider, initialSettings));
    setDisplayNameEdited(false);
    setSettings(initialSettings);
    setCustomEndpoint("");
    setCustomModel("");
    setStartNow(provider.startable);
  }

  function chooseProviderGroup(group: ProviderCatalogGroup) {
    setProviderGroup(group);
    setProviderId("");
    setExistingSessionId("");
    setDisplayName("");
    setDisplayNameEdited(false);
    setSettings({});
    setCustomEndpoint("");
    setCustomModel("");
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
      max_output_tokens: String(session.max_output_tokens || ""),
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
        modelId: selectedProvider.custom_model ? customModel.trim() : settings.model || "",
        providerEndpoint: selectedProvider.custom_endpoint
          ? customEndpoint.trim()
          : "",
        reasoningEffort: settings.reasoning_effort || "",
        serviceTier: settings.service_tier || "",
        variant: settings.variant || "",
        permissionMode: settings.permission_mode || "meeting_read_only",
        maxOutputTokens: Number(settings.max_output_tokens || 0),
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

  async function startProviderLogin() {
    if (!selectedProvider?.login_available || loginBusy) return;
    setLoginBusy(true);
    setStatus(
      selectedProvider.login_flow === "browser_oauth"
        ? "브라우저에서 로그인 중..."
        : ""
    );
    try {
      const result = await startFrontendLiveAgentLogin(selectedProvider.id);
      setStatus(
        result.message ||
          `${selectedProvider.display_name} 로그인 창을 열었습니다.`
      );
    } catch (error) {
      setStatus(
        error instanceof Error
          ? error.message
          : `${selectedProvider.display_name} 로그인을 시작하지 못했습니다`
      );
    } finally {
      setLoginBusy(false);
    }
  }

  async function recheckProviderLogin() {
    if (!selectedProvider || loginBusy) return;
    setLoginBusy(true);
    setStatus("로그인 상태 확인 중...");
    try {
      const catalog = await refreshProviderCatalog();
      const refreshed = catalog.providers.find(
        (provider) => provider.id === selectedProvider.id
      );
      setStatus(
        refreshed?.discovery_status === "ready"
          ? `${selectedProvider.display_name} 로그인 확인 완료`
          : refreshed?.discovery_error || "로그인 상태를 확인하지 못했습니다"
      );
    } catch (error) {
      setStatus(
        error instanceof Error ? error.message : "로그인 상태 확인 실패"
      );
    } finally {
      setLoginBusy(false);
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
          setStatus("");
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
              {PROVIDER_GROUPS.map(({ id, label, Icon }) => (
                <button
                  key={id}
                  type="button"
                  role="listitem"
                  aria-label={label}
                  data-active={providerGroup === id}
                  disabled={groupedProviders[id].length === 0}
                  onClick={() => chooseProviderGroup(id)}
                >
                  <Icon size={22} aria-hidden="true" />
                  <span>{label}</span>
                </button>
              ))}
            </div>
          </section>

          {providerGroup && (
            <section className="dc-agent-section">
              <p className="dc-agent-section-title">
                {providerGroupLabel(providerGroup)} Providers
              </p>
              <div
                className="dc-agent-provider-grid"
                role="list"
                aria-label={
                  providerGroup === "api"
                    ? "API 프로바이더"
                    : `${providerGroupLabel(providerGroup)} Providers`
                }
              >
                {visibleProviders.map(renderProviderChoice)}
              </div>
            </section>
          )}

          <section className="dc-agent-section">
            <p className="dc-agent-section-title">기본 정보</p>
            <div className="dc-agent-field-grid">
              {reusableSessions.length > 0 && (
                <label className="dc-agent-field">
                  <span>기존 세션</span>
                  <ProviderControlSelect
                    label="기존 세션"
                    options={[
                      { value: "", label: "새 세션 만들기" },
                      ...reusableSessions.map((session) => ({
                        value: session.session_id,
                        label: `${session.display_name} · ${session.model || session.provider_kind}`,
                      })),
                    ]}
                    value={existingSessionId}
                    onChange={applyExistingSession}
                  />
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
              {!existingSessionId && selectedProvider?.workspace_required !== false && (
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

          {selectedProvider?.custom_endpoint && !existingSessionId && (
            <section className="dc-agent-section">
              <p className="dc-agent-section-title">API 연결</p>
              <div className="dc-agent-field-grid dc-agent-field-grid--dual">
                <label className="dc-agent-field">
                  <span>API 주소</span>
                  <input
                    type="url"
                    value={customEndpoint}
                    placeholder="https://example.com/v1 또는 …/chat/completions"
                    onChange={(event) => setCustomEndpoint(event.currentTarget.value)}
                  />
                </label>
                <label className="dc-agent-field">
                  <span>모델 ID</span>
                  <input
                    value={customModel}
                    placeholder="provider가 요구하는 정확한 모델 ID"
                    onChange={(event) => {
                      const value = event.currentTarget.value;
                      setCustomModel(value);
                      if (!displayNameEdited) {
                        setDisplayName(value.trim() ? `Custom ${value.trim()}` : "Custom API");
                      }
                    }}
                  />
                </label>
              </div>
              <p className="preserve-words">
                Base URL과 /chat/completions 전체 주소를 모두 받을 수 있습니다.
              </p>
            </section>
          )}

          {selectedProvider && selectedProvider.controls.length > 0 && (
            <section className="dc-agent-section">
              <p className="dc-agent-section-title">모델 · 실행 설정</p>
              <div className="dc-agent-field-grid dc-agent-field-grid--dual">
                {displayProviderControls(selectedProvider).map((control) => {
                  const providerSupportsControl = selectedProvider.controls.some(
                    (candidate) => candidate.key === control.key
                  );
                  const options = providerSupportsControl
                    ? effectiveProviderControlOptions(
                        selectedProvider,
                        control,
                        settings
                      )
                    : control.options;
                  return (
                    <ProviderControlField
                      key={`${selectedProvider.id}:${control.key}`}
                      control={control}
                      options={options}
                      value={
                        providerSupportsControl
                          ? settings[control.key] ?? control.default_value
                          : control.default_value
                      }
                      disabled={Boolean(existingSessionId) || !providerSupportsControl}
                      onChange={(value) => updateProviderControl(control.key, value)}
                    />
                  );
                })}
              </div>
            </section>
          )}

          {selectedProvider && providerCatalogGroup(selectedProvider) === "api" && (
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

          {selectedProvider?.login_available &&
            selectedProvider.discovery_error_code ===
              "authentication_required" && (
              <section className="dc-agent-section">
                <p className="dc-agent-section-title">인증</p>
                <div className="dc-provider-secret-field">
                  <div>
                    <button
                      type="button"
                      disabled={loginBusy}
                      onClick={() => void startProviderLogin()}
                    >
                      {loginBusy
                        ? selectedProvider.login_flow === "browser_oauth"
                          ? "로그인 중..."
                          : "처리 중..."
                        : selectedProvider.login_label ||
                          `${selectedProvider.display_name} 로그인`}
                    </button>
                    {selectedProvider.login_flow ===
                      "interactive_terminal" && (
                      <button
                        type="button"
                        disabled={loginBusy}
                        onClick={() => void recheckProviderLogin()}
                      >
                        로그인 완료 후 다시 확인
                      </button>
                    )}
                  </div>
                  <p>
                    {selectedProvider.login_flow === "browser_oauth"
                      ? "브라우저 인증이 끝나면 모델 목록을 자동으로 다시 확인합니다."
                      : "대화형 로그인이 끝나면 상태를 다시 확인하세요."}{" "}
                    인증 정보는 AgentsAssemble에 저장하지 않습니다.
                  </p>
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
      {control.key === "service_tier" && options.length <= 2 ? (
        <ProviderControlToggle
          label={control.label}
          options={options}
          value={value}
          disabled={disabled}
          onChange={onChange}
        />
      ) : (
        <ProviderControlSelect
          label={control.label}
          options={options}
          value={value}
          disabled={disabled}
          onChange={onChange}
        />
      )}
    </label>
  );
}

function deriveStatusMessage({
  status,
  workspacePath,
  selectedProvider,
  selectedProviderMissing,
  hasProviders,
  invalidControl,
  existingSessionId,
}: {
  status: string;
  workspacePath: string;
  selectedProvider: NativeCliProviderAvailability | undefined;
  selectedProviderMissing: boolean;
  hasProviders: boolean;
  invalidControl: ProviderControl | undefined;
  existingSessionId: string;
}): string {
  if (status) return status;
  if (selectedProvider && !selectedProvider.available) {
    return selectedProvider.discovery_error || "CLI를 찾지 못했습니다";
  }
  if (selectedProvider?.discovery_status === "loading") {
    return "모델 목록을 불러오는 중입니다";
  }
  if (
    selectedProvider?.catalog_source === "stale_cache" &&
    selectedProvider.discovery_error
  ) {
    return selectedProvider.discovery_error;
  }
  if (selectedProvider?.discovery_status === "failed" && selectedProvider.available) {
    return selectedProvider.discovery_error || "모델 목록을 불러오지 못했습니다";
  }
  if (selectedProviderMissing) {
    return "선택한 provider가 현재 catalog에 없습니다.";
  }
  if (!selectedProvider && !selectedProviderMissing && hasProviders) {
    return "사용할 provider를 선택하세요.";
  }
  if (!existingSessionId && invalidControl) {
    return `${invalidControl.label}의 유효한 기본값이 없어 직접 선택해야 합니다.`;
  }
  // The confirm button is disabled until a workspace is picked, and without
  // this the button just sits dead with nothing explaining why.
  if (
    !existingSessionId &&
    selectedProvider &&
    selectedProvider.workspace_required !== false &&
    !workspacePath.trim()
  ) {
    return "작업 폴더를 선택하세요. 이 폴더에서 세션이 실행됩니다.";
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
