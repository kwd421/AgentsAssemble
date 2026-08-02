import { useState } from "react";
import { Copy, KeyRound, Link2 } from "lucide-react";

import { issueGuestRecoveryCode, type UserProfileIdentity } from "../../api";

export default function GuestRecoverySettings({
  identity,
}: {
  identity: UserProfileIdentity;
}) {
  const [code, setCode] = useState("");
  const [recoveryUrl, setRecoveryUrl] = useState("");
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);

  async function issue() {
    if (!identity.sessionToken || busy) return;
    setBusy(true);
    setStatus("복구 코드를 만드는 중...");
    try {
      const result = await issueGuestRecoveryCode({
        sessionToken: identity.sessionToken,
        deviceToken: identity.deviceToken,
      });
      setCode(result.recovery_code);
      setRecoveryUrl(result.recovery_url);
      setStatus("새 코드가 발급됐습니다. 이전 복구 코드는 더 이상 사용할 수 없습니다.");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "복구 코드를 만들지 못했습니다.");
    } finally {
      setBusy(false);
    }
  }

  async function copy(value: string, label: string) {
    try {
      await navigator.clipboard.writeText(value);
      setStatus(`${label}을 복사했습니다.`);
    } catch {
      setStatus("값을 직접 선택해 복사해 주세요.");
    }
  }

  return (
    <div className="dc-guest-recovery-settings">
      <header>
        <KeyRound size={18} />
        <div>
          <h3>게스트 신원 복구</h3>
          <p>다른 기기에서도 지금 이름과 방 멤버십을 그대로 이어갑니다.</p>
        </div>
      </header>
      <p className="dc-guest-recovery-warning">
        새 코드를 만들면 이전 코드는 즉시 폐기됩니다. 서버에는 코드 원문을 저장하지 않습니다.
      </p>
      <button type="button" className="dc-guest-recovery-issue" disabled={busy} onClick={() => void issue()}>
        <KeyRound size={16} />
        {busy ? "발급 중..." : code ? "새 코드로 교체" : "복구 코드 만들기"}
      </button>
      {code && (
        <div className="dc-guest-recovery-result">
          <label>
            일회용 복구 코드
            <span>
              <input value={code} readOnly spellCheck={false} />
              <button type="button" aria-label="복구 코드 복사" onClick={() => void copy(code, "복구 코드")}>
                <Copy size={16} />
              </button>
            </span>
          </label>
          <button type="button" onClick={() => void copy(recoveryUrl, "복구 링크")}>
            <Link2 size={16} />
            복구 링크 복사
          </button>
        </div>
      )}
      {status && <p className="dc-member-session-status preserve-words">{status}</p>}
    </div>
  );
}
