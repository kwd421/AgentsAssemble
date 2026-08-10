import { useState } from "react";
import type { ProviderCredentialStatus } from "../../api";
import type { NativeCliProviderAvailability } from "../../roomSocketClient";

type ProviderCredentialFieldProps = {
  provider: NativeCliProviderAvailability;
  status: ProviderCredentialStatus | null;
  value: string;
  busy: boolean;
  onValueChange: (value: string) => void;
  onSave: (options?: { workspaceId?: string }) => void;
  onDelete: () => void;
};

export default function ProviderCredentialField({
  provider,
  status,
  value,
  busy,
  onValueChange,
  onSave,
  onDelete,
}: ProviderCredentialFieldProps) {
  const isOpenCode = provider.id === "opencode";
  const [workspaceId, setWorkspaceId] = useState("");

  return (
    <section className="dc-agent-section">
      <p className="dc-agent-section-title">인증</p>
      <div className="dc-provider-secret-field">
        {isOpenCode && (
          <label className="dc-agent-field">
            <span>OpenCode Go workspace ID</span>
            <input
              type="text"
              autoComplete="off"
              value={workspaceId}
              placeholder="wrk_… 또는 대시보드 URL"
              onChange={(event) => setWorkspaceId(event.currentTarget.value)}
            />
          </label>
        )}
        <label className="dc-agent-field">
          <span>{isOpenCode ? "OpenCode Go 세션 쿠키" : "API 키"}</span>
          <input
            type="password"
            autoComplete="off"
            value={value}
            placeholder={
              status?.configured
                ? "설정됨"
                : isOpenCode
                  ? "auth 또는 __Host-auth 쿠키"
                  : `${provider.display_name} API key`
            }
            onChange={(event) => onValueChange(event.currentTarget.value)}
          />
        </label>
        <div>
          <button
            type="button"
            disabled={!value.trim() || busy || (isOpenCode && !workspaceId.trim())}
            onClick={() => onSave(isOpenCode ? { workspaceId } : undefined)}
          >
            보안 저장
          </button>
          {status?.source === "keyring" && (
            <button type="button" disabled={busy} onClick={onDelete}>
              저장 키 삭제
            </button>
          )}
        </div>
        <p>
          {status?.configured
            ? `${isOpenCode ? "인증 정보" : "키"} 설정됨 · ${status.source}`
            : isOpenCode
              ? "Go 대시보드 URL의 workspace ID와 auth 쿠키를 직접 저장하세요. 브라우저에서는 자동으로 가져오지 않으며 Zen 사용량에는 적용되지 않습니다."
              : "키 없음"}
        </p>
      </div>
    </section>
  );
}
