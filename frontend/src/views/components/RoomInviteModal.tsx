import { Copy, X } from "lucide-react";

export default function RoomInviteModal({
  roomLabel,
  inviteUrl,
  copyStatus,
  onClose,
  onCopy,
}: {
  roomLabel: string;
  inviteUrl: string;
  copyStatus?: string;
  onClose: () => void;
  onCopy: () => void;
}) {
  return (
    <div className="dc-modal-backdrop" role="presentation" onClick={onClose}>
      <section
        className="dc-invite-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="room-invite-title"
        onClick={(event) => event.stopPropagation()}
      >
        <header className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <h2 id="room-invite-title" className="truncate text-[18px] font-black text-text-primary preserve-words">
              {roomLabel}에 초대하기
            </h2>
            <p className="mt-1 text-[13px] text-text-muted preserve-words">
              이 링크로 들어온 사람은 이 방만 보고 채팅합니다.
            </p>
          </div>
          <button
            type="button"
            className="dc-modal-close"
            onClick={onClose}
            aria-label="초대 닫기"
          >
            <X size={18} />
          </button>
        </header>
        <label className="mt-5 grid gap-2 text-[12px] font-bold text-text-muted">
          초대 링크
          <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_112px]">
            <input
              className="dc-invite-link-input"
              value={inviteUrl}
              readOnly
              onFocus={(event) => event.currentTarget.select()}
            />
            <button type="button" className="dc-invite-copy-button" onClick={onCopy}>
              <Copy size={15} />
              링크 복사
            </button>
          </div>
        </label>
        <p className="mt-3 text-[12px] text-text-muted preserve-words">
          {copyStatus || "브라우저 게스트 링크입니다. provider/CLI 실행 권한은 주지 않습니다."}
        </p>
      </section>
    </div>
  );
}
