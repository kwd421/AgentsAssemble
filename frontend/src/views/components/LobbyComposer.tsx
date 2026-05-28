import { useRef, useState, type ChangeEvent, type KeyboardEvent } from "react";
import { Paperclip, Send, X } from "lucide-react";
import {
  postLobbyMessage,
  uploadLobbyAttachment,
  type LobbyAttachmentRef,
  type LobbyEvent,
} from "../../api";
import {
  MAX_ATTACHMENTS_MESSAGE,
  MAX_ATTACHMENTS_PER_EVENT,
  lobbySubmitFailureDraft,
  lobbySubmitSuccessDraft,
  selectLobbyAttachmentFiles,
} from "../../lib/lobbyComposerModel";

function currentLobbyName() {
  try {
    return window.localStorage.getItem("agentsassemble.name") || "나";
  } catch {
    return "나";
  }
}

export default function LobbyComposer({
  onPosted,
}: {
  onPosted: (events: LobbyEvent[]) => void;
}) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [message, setMessage] = useState("");
  const [pendingAttachments, setPendingAttachments] = useState<LobbyAttachmentRef[]>([]);
  const [busy, setBusy] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");
  const canSubmit = Boolean(message.trim() || pendingAttachments.length) && !busy && !uploading;

  async function handleFileChange(event: ChangeEvent<HTMLInputElement>) {
    const selected = Array.from(event.currentTarget.files || []);
    event.currentTarget.value = "";
    if (!selected.length) return;

    const { accepted: filesToUpload, error: selectionError } = selectLobbyAttachmentFiles(
      pendingAttachments.length,
      selected
    );
    if (filesToUpload.length === 0) {
      setError(selectionError || MAX_ATTACHMENTS_MESSAGE);
      return;
    }
    setError(selectionError);

    setUploading(true);
    try {
      const uploaded: LobbyAttachmentRef[] = [];
      for (const file of filesToUpload) {
        uploaded.push(await uploadLobbyAttachment(file));
      }
      setPendingAttachments((current) =>
        [...current, ...uploaded].slice(0, MAX_ATTACHMENTS_PER_EVENT)
      );
    } catch (errorValue) {
      setError(errorValue instanceof Error ? errorValue.message : "첨부 업로드 실패");
    } finally {
      setUploading(false);
    }
  }

  function removePendingAttachment(attachmentId: string) {
    if (busy || uploading) return;
    setPendingAttachments((current) =>
      current.filter((attachment) => attachment.id !== attachmentId)
    );
  }

  async function handleSubmit() {
    if (busy || uploading) return;
    const draftMessage = message;
    const draftAttachments = pendingAttachments;
    const trimmed = draftMessage.trim();
    if (!trimmed && draftAttachments.length === 0) return;

    setBusy(true);
    setError("");
    try {
      const payload = await postLobbyMessage({
        name: currentLobbyName(),
        side: "mine",
        kind: "message",
        message: trimmed,
        attachments: draftAttachments,
      });
      const cleared = lobbySubmitSuccessDraft<LobbyAttachmentRef>();
      setMessage(cleared.message);
      setPendingAttachments(cleared.pendingAttachments);
      onPosted(payload.events || (payload.event ? [payload.event] : []));
    } catch (errorValue) {
      const restored = lobbySubmitFailureDraft(
        draftMessage,
        draftAttachments,
        errorValue instanceof Error ? errorValue.message : "로비 메시지 전송 실패"
      );
      setMessage(restored.message);
      setPendingAttachments(restored.pendingAttachments);
      setError(restored.error);
    } finally {
      setBusy(false);
    }
  }

  function handleKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key !== "Enter" || event.nativeEvent.isComposing) return;
    event.preventDefault();
    void handleSubmit();
  }

  return (
    <section className="ops-panel ops-cut p-4">
      <div className="mb-3 flex items-center justify-between gap-3">
        <h2 className="text-[15px] font-black">로비 채팅</h2>
        <span className="text-[11px] font-bold text-text-muted">
          {pendingAttachments.length}/{MAX_ATTACHMENTS_PER_EVENT}
        </span>
      </div>

      {error && (
        <p className="mb-3 rounded-lg border border-danger/30 bg-danger/10 px-3 py-2 text-[12px] font-semibold text-danger preserve-words">
          {error}
        </p>
      )}

      {pendingAttachments.length > 0 && (
        <div className="mb-3 flex flex-wrap gap-2">
          {pendingAttachments.map((attachment) => (
            <span
              key={attachment.id}
              className="ops-inner inline-flex max-w-full items-center gap-2 rounded-lg px-3 py-1.5 text-[12px] font-bold text-text-secondary"
            >
              <span className="min-w-0 truncate preserve-words">{attachment.filename}</span>
              <button
                type="button"
                onClick={() => removePendingAttachment(attachment.id)}
                disabled={busy || uploading}
                className="grid h-5 w-5 shrink-0 place-items-center rounded border border-line/70 text-text-muted hover:border-danger/45 hover:text-danger"
                aria-label={`${attachment.filename} 첨부 제거`}
              >
                <X size={12} />
              </button>
            </span>
          ))}
        </div>
      )}

      <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_44px_44px]">
        <input
          value={message}
          onChange={(event) => setMessage(event.target.value)}
          onKeyDown={handleKeyDown}
          className="ops-input min-w-0 rounded-lg px-3 py-2.5 text-[13px]"
          placeholder={uploading ? "첨부 업로드 중..." : "로비에 메시지 남기기..."}
          disabled={busy}
        />
        <input
          ref={fileInputRef}
          type="file"
          multiple
          className="hidden"
          onChange={handleFileChange}
          aria-label="로비 첨부 선택"
        />
        <button
          type="button"
          onClick={() => fileInputRef.current?.click()}
          disabled={busy || uploading || pendingAttachments.length >= MAX_ATTACHMENTS_PER_EVENT}
          className="grid h-11 place-items-center rounded-lg border border-accent/25 bg-accent/5 text-accent disabled:opacity-40"
          aria-label="첨부 추가"
        >
          <Paperclip size={17} />
        </button>
        <button
          type="button"
          onClick={handleSubmit}
          disabled={!canSubmit}
          className="grid h-11 place-items-center rounded-lg border border-accent/35 bg-accent/10 text-accent disabled:opacity-40"
          aria-label="로비 메시지 보내기"
        >
          <Send size={17} />
        </button>
      </div>
    </section>
  );
}
