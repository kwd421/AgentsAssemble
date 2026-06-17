import { useEffect, useMemo, useState } from "react";
import { Bot, CheckCircle2, Folder, LogIn, Play, Plus, X } from "lucide-react";
import {
  checkFrontendLiveAgent,
  createFrontendLiveAgent,
  fetchLiveAgentCreateOptions,
  fetchProviderSessions,
  startFrontendLiveAgentLogin,
  type FrontendLiveAgentCreateResponse,
  type FrontendLiveAgentCheckResponse,
  type LiveAgentCreateProvider,
  type ProviderSession,
} from "../../api";

type AgentCreateModalProps = {
  open: boolean;
  meetingId: string;
  roomLabel: string;
  onClose: () => void;
  onCreated?: (result: FrontendLiveAgentCreateResponse) => void;
};

export default function AgentCreateModal({
  open,
  meetingId,
  roomLabel,
  onClose,
  onCreated,
}: AgentCreateModalProps) {
  const [providers, setProviders] = useState<LiveAgentCreateProvider[]>([]);
  const [providerId, setProviderId] = useState("codex");
  const [displayName, setDisplayName] = useState("Codex");
  const [workspacePath, setWorkspacePath] = useState("");
  const [modelId, setModelId] = useState("");
  const [effort, setEffort] = useState("");
  const [speed, setSpeed] = useState("balanced");
  const [permissionOption, setPermissionOption] = useState("");
  const [replyCharLimit, setReplyCharLimit] = useState(0);
  const [fastMode, setFastMode] = useState(false);
  const [sessions, setSessions] = useState<ProviderSession[]>([]);
  const [sessionId, setSessionId] = useState("");
  const [startNow, setStartNow] = useState(true);
  const [status, setStatus] = useState("");
  const [authAction, setAuthAction] = useState<FrontendLiveAgentCheckResponse["auth_action"] | null>(null);
  const [busy, setBusy] = useState<"check" | "create" | "login" | "">("");

  useEffect(() => {
    if (!open) return;
    setStatus("");
    fetchLiveAgentCreateOptions()
      .then((payload) => {
        setProviders(payload.providers || []);
        setWorkspacePath((previous) => previous || payload.default_workspace || "");
        const first = payload.providers?.[0];
        if (first) {
          applyProviderDefaults(first);
        }
      })
      .catch((error) => setStatus(error instanceof Error ? error.message : "에이전트 옵션을 불러오지 못했습니다"));
  }, [open]);

  const selectedProvider = useMemo(
    () => providers.find((provider) => provider.id === providerId) || providers[0],
    [providerId, providers]
  );
  const modelOptions = selectedProvider?.model_options || [];
  const effortOptions = selectedProvider?.effort_options || [];
  const permissionOptions = selectedProvider?.permission_options || [];
  // Fast toggle only where the CLI has one: codex (--enable fast_mode), claude (/fast).
  const supportsFast =
    selectedProvider?.provider_kind === "codex_live_session" ||
    selectedProvider?.provider_kind === "claude_code";
  const canCreate = Boolean(meetingId && providerId && displayName.trim() && workspacePath.trim());
  const effectiveStartNow = Boolean(startNow && selectedProvider?.startable);

  // Local sessions for the selected provider so the user can resume one.
  useEffect(() => {
    setSessionId("");
    const kind = selectedProvider?.provider_kind;
    if (!open || !kind) {
      setSessions([]);
      return;
    }
    let cancelled = false;
    fetchProviderSessions(kind, workspacePath)
      .then((payload) => {
        if (!cancelled) setSessions(payload.sessions || []);
      })
      .catch(() => {
        if (!cancelled) setSessions([]);
      });
    return () => {
      cancelled = true;
    };
  }, [open, selectedProvider?.provider_kind, workspacePath]);

  function firstOptionId(
    options: Array<{ id: string; label: string }> | undefined,
    fallback = ""
  ) {
    return options?.[0]?.id ?? fallback;
  }

  function applyProviderDefaults(provider: LiveAgentCreateProvider) {
    setProviderId(provider.id);
    setDisplayName(provider.label);
    setModelId(firstOptionId(provider.model_options));
    setEffort(firstOptionId(provider.effort_options));
    setSpeed(firstOptionId(provider.speed_options, "balanced"));
    setPermissionOption(firstOptionId(provider.permission_options));
    setFastMode(false);
    if (!provider.startable) setStartNow(false);
  }

  function selectProvider(nextProviderId: string) {
    const next = providers.find((provider) => provider.id === nextProviderId);
    if (next) {
      applyProviderDefaults(next);
    } else {
      setProviderId(nextProviderId);
      setModelId("");
      setEffort("");
      setSpeed("balanced");
    }
    setStatus("");
    setAuthAction(null);
  }

  async function handleCheck() {
    if (!canCreate) {
      setStatus("이름과 폴더를 입력하세요");
      return;
    }
    setBusy("check");
    setStatus("연결 확인 중...");
    try {
      const result = await checkFrontendLiveAgent({
        meetingId,
        providerId,
        displayName,
        workspacePath,
        modelId,
        effort,
        speed,
        replyCharLimit,
        permissionOption,
        fastMode,
        sessionId,
        startNow: effectiveStartNow,
      });
      setAuthAction(result.auth_action || null);
      setStatus(result.status === "ok" ? "연결 확인 완료" : result.message || "연결 확인 실패");
    } catch (error) {
      setAuthAction(null);
      setStatus(error instanceof Error ? error.message : "연결 확인 실패");
    } finally {
      setBusy("");
    }
  }

  async function handleLogin() {
    const targetProviderId = authAction?.provider_id || providerId;
    setBusy("login");
    setStatus("로그인 창을 여는 중...");
    try {
      const result = await startFrontendLiveAgentLogin(targetProviderId);
      setStatus(result.message || "로그인 창을 열었습니다. 로그인 완료 후 연결 확인을 다시 누르세요.");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "로그인 창을 열지 못했습니다");
    } finally {
      setBusy("");
    }
  }

  async function handleCreate() {
    if (!canCreate) {
      setStatus("이름과 폴더를 입력하세요");
      return;
    }
    setBusy("create");
    setStatus(effectiveStartNow ? "에이전트 시작 중..." : "에이전트 추가 중...");
    try {
      const result = await createFrontendLiveAgent({
        meetingId,
        providerId,
        displayName,
        workspacePath,
        modelId,
        effort,
        speed,
        replyCharLimit,
        permissionOption,
        fastMode,
        sessionId,
        startNow: effectiveStartNow,
      });
      setStatus(result.status === "starting" ? "시작 요청 완료" : "추가됨");
      onCreated?.(result);
      onClose();
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "에이전트 추가 실패");
    } finally {
      setBusy("");
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
              data-active={provider.id === providerId}
              onClick={() => selectProvider(provider.id)}
            >
              <Bot size={16} />
              <span>{provider.label}</span>
            </button>
          ))}
        </div>

        <div className="dc-agent-create-fields">
          <label>
            <span>이름</span>
            <input
              value={displayName}
              onChange={(event) => setDisplayName(event.currentTarget.value)}
              placeholder="에이전트 이름"
            />
          </label>
          <label>
            <span>폴더</span>
            <div className="dc-agent-folder-field">
              <Folder size={16} />
              <input
                value={workspacePath}
                onChange={(event) => setWorkspacePath(event.currentTarget.value)}
                placeholder="/Users/seinel/Projects/AgentCouncil"
              />
            </div>
          </label>
          {modelOptions.length > 0 && (
            <label>
              <span>모델</span>
              <select value={modelId} onChange={(event) => setModelId(event.currentTarget.value)}>
                {modelOptions.map((option) => (
                  <option key={`${providerId}:model:${option.id || "default"}`} value={option.id}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>
          )}
          {effortOptions.length > 0 && (
            <label>
              <span>추론 강도</span>
              <select value={effort} onChange={(event) => setEffort(event.currentTarget.value)}>
                {effortOptions.map((option) => (
                  <option key={`${providerId}:effort:${option.id || "default"}`} value={option.id}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>
          )}
          {permissionOptions.length > 0 && (
            <label>
              <span>권한</span>
              <select value={permissionOption} onChange={(event) => setPermissionOption(event.currentTarget.value)}>
                {permissionOptions.map((option) => (
                  <option key={`${providerId}:perm:${option.id}`} value={option.id}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>
          )}
          <label>
            <span>답변 길이</span>
            <select
              value={String(replyCharLimit)}
              onChange={(event) => setReplyCharLimit(Number(event.currentTarget.value))}
            >
              <option value="0">기본 (제한 없음)</option>
              <option value="100">100자</option>
              <option value="250">250자</option>
              <option value="400">400자</option>
              <option value="700">700자</option>
              <option value="1000">1000자</option>
            </select>
          </label>
          {sessions.length > 0 && (
            <label>
              <span>세션</span>
              <select value={sessionId} onChange={(event) => setSessionId(event.currentTarget.value)}>
                <option value="">새 세션</option>
                {sessions.map((session) => (
                  <option key={session.session_id} value={session.session_id}>
                    {`${(session.label || session.session_id).slice(0, 40)} · ${session.updated_at.slice(5, 16).replace("T", " ")}`}
                  </option>
                ))}
              </select>
            </label>
          )}
          {supportsFast && (
            <label className="dc-agent-start-toggle">
              <input
                type="checkbox"
                checked={fastMode}
                onChange={(event) => setFastMode(event.currentTarget.checked)}
              />
              <span>빠른 모드 (fast)</span>
            </label>
          )}
          <label className="dc-agent-start-toggle">
            <input
              type="checkbox"
              checked={effectiveStartNow}
              disabled={!selectedProvider?.startable}
              onChange={(event) => setStartNow(event.currentTarget.checked)}
            />
            <span>{selectedProvider?.startable ? "바로 시작" : "준비 중"}</span>
          </label>
        </div>

        {selectedProvider?.verification_note && (
          <p className="dc-agent-create-note preserve-words">{selectedProvider.verification_note}</p>
        )}
        {status && <p className="dc-agent-create-status preserve-words">{status}</p>}
        {authAction && (
          <button
            type="button"
            className="dc-agent-login-action"
            onClick={handleLogin}
            disabled={busy !== ""}
          >
            <LogIn size={16} />
            {authAction.label}
          </button>
        )}

        <footer className="dc-agent-create-actions">
          <button type="button" onClick={handleCheck} disabled={busy !== "" || !canCreate}>
            <CheckCircle2 size={16} />
            연결 확인
          </button>
          <button
            type="button"
            className="primary"
            onClick={handleCreate}
            disabled={busy !== "" || !canCreate}
          >
            {effectiveStartNow ? <Play size={16} /> : <Plus size={16} />}
            {effectiveStartNow ? "추가하고 시작" : "추가"}
          </button>
        </footer>
      </section>
    </div>
  );
}
