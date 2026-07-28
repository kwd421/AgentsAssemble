import { useEffect, useId, useRef, useState } from "react";
import { LogOut, X } from "lucide-react";

export default function LeaveRoomDialog({
  roomLabel,
  onClose,
  onConfirm,
}: {
  roomLabel: string;
  onClose: () => void;
  onConfirm: () => Promise<void>;
}) {
  const titleId = useId();
  const cancelRef = useRef<HTMLButtonElement>(null);
  const busyRef = useRef(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    busyRef.current = busy;
  }, [busy]);

  useEffect(() => {
    const previouslyFocused = document.activeElement as HTMLElement | null;
    cancelRef.current?.focus();

    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === "Escape" && !busyRef.current) onClose();
    }

    window.addEventListener("keydown", closeOnEscape);
    return () => {
      window.removeEventListener("keydown", closeOnEscape);
      if (previouslyFocused?.isConnected) previouslyFocused.focus();
    };
  }, [onClose]);

  async function confirmLeave() {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await onConfirm();
      onClose();
    } catch (errorValue) {
      setError(
        errorValue instanceof Error
          ? errorValue.message
          : "서버에서 나가지 못했습니다."
      );
      setBusy(false);
    }
  }

  return (
    <div
      className="dc-modal-backdrop"
      role="presentation"
      onMouseDown={() => {
        if (!busy) onClose();
      }}
    >
      <section
        className="dc-create-channel-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="dc-create-channel-head">
          <h2 id={titleId}>{roomLabel} 서버에서 나갈까요?</h2>
          <button
            type="button"
            className="dc-settings-close"
            onClick={onClose}
            disabled={busy}
            aria-label="서버 나가기 취소"
          >
            <X size={18} />
          </button>
        </header>

        <div className="grid gap-2 text-[14px] leading-6 text-text-muted">
          <p className="preserve-words">
            나간 뒤 다시 들어오려면 유효한 초대 링크가 필요합니다.
          </p>
          <p className="preserve-words font-bold text-text-primary">
            내가 소유한 에이전트도 모두 함께 나가며, 실행 중인 Agent Session은 종료됩니다.
          </p>
          <p className="preserve-words">
            나와 에이전트가 남긴 기존 대화 기록은 서버에 보존됩니다.
          </p>
        </div>

        {error && (
          <p className="dc-channel-composer-error preserve-words" role="alert">
            {error}
          </p>
        )}

        <div className="dc-create-channel-actions">
          <button
            ref={cancelRef}
            type="button"
            className="ops-button"
            onClick={onClose}
            disabled={busy}
          >
            취소
          </button>
          <button
            type="button"
            className="ops-cta dc-leave-room-confirm inline-flex items-center justify-center gap-2"
            onClick={() => void confirmLeave()}
            disabled={busy}
          >
            <LogOut size={16} />
            {busy ? "나가는 중..." : "서버 나가기"}
          </button>
        </div>
      </section>
    </div>
  );
}
