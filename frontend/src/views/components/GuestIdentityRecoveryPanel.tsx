import { useState } from "react";
import { Check, Copy, KeyRound, LoaderCircle } from "lucide-react";

import {
  redeemGuestRecoveryCode,
  type GuestRecoveryRedeemResponse,
} from "../../api";
import { getOrCreateClientId, getOrCreateDeviceToken } from "../../lib/deviceIdentity";
import type { GuestRecoveryRequest } from "../../lib/guestRecovery";

export default function GuestIdentityRecoveryPanel({
  request,
  onRecovered,
}: {
  request: GuestRecoveryRequest;
  onRecovered: (payload: GuestRecoveryRedeemResponse) => void;
}) {
  const [recoveryCode, setRecoveryCode] = useState(request.recoveryCode);
  const [recovered, setRecovered] = useState<GuestRecoveryRedeemResponse | null>(null);
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);

  async function recover() {
    if (busy) return;
    setBusy(true);
    setStatus("기존 신원과 방 멤버십을 확인하는 중...");
    try {
      const payload = await redeemGuestRecoveryCode({
        recoveryCode,
        roomId: request.roomId,
        deviceToken: getOrCreateDeviceToken(),
        clientId: getOrCreateClientId(),
      });
      setRecovered(payload);
      setRecoveryCode(payload.recovery_code);
      setStatus("");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "신원을 복구하지 못했습니다.");
    } finally {
      setBusy(false);
    }
  }

  async function copyReplacementCode() {
    if (!recovered) return;
    try {
      await navigator.clipboard.writeText(recovered.recovery_code);
      setCopied(true);
    } catch {
      setStatus("코드를 직접 선택해 복사해 주세요.");
    }
  }

  return (
    <div className="dc-guest-join-panel">
      <section className="dc-guest-recovery-card" aria-label="게스트 신원 복구">
        <span className="dc-guest-recovery-icon" aria-hidden>
          {recovered ? <Check size={24} /> : <KeyRound size={24} />}
        </span>
        <div>
          <h1>{recovered ? "신원을 복구했습니다" : "기존 신원으로 돌아가기"}</h1>
          <p>
            {recovered
              ? `${recovered.display_name} 이름과 기존 방 멤버십을 이 기기에 연결했습니다.`
              : "복구 코드는 기존 게스트 이름과 방 멤버십을 이 기기에 연결합니다."}
          </p>
        </div>

        <label className="dc-guest-recovery-code">
          {recovered ? "새 복구 코드" : "복구 코드"}
          <input
            value={recoveryCode}
            readOnly={Boolean(recovered)}
            autoComplete="one-time-code"
            spellCheck={false}
            onChange={(event) => setRecoveryCode(event.currentTarget.value)}
          />
        </label>

        {recovered ? (
          <>
            <p className="dc-guest-recovery-warning">
              이전 코드는 폐기됐습니다. 다음 복구에 쓸 새 코드를 지금 안전한 곳에 보관하세요.
            </p>
            <div className="dc-guest-recovery-actions">
              <button type="button" onClick={() => void copyReplacementCode()}>
                <Copy size={16} />
                {copied ? "복사됨" : "새 코드 복사"}
              </button>
              <button type="button" onClick={() => onRecovered(recovered)}>
                방으로 계속
              </button>
            </div>
          </>
        ) : (
          <button
            type="button"
            className="dc-guest-recovery-submit"
            disabled={busy || !recoveryCode.trim()}
            onClick={() => void recover()}
          >
            {busy ? <LoaderCircle className="animate-spin" size={16} /> : <KeyRound size={16} />}
            신원 복구
          </button>
        )}
        {status && <p className="dc-member-session-status preserve-words">{status}</p>}
      </section>
    </div>
  );
}
