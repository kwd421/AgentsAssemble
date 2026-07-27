import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ChangeEvent,
  type KeyboardEvent,
} from "react";
import type { LucideIcon } from "lucide-react";
import { AtSign, Gift, Paperclip, Send, Smile, Sparkles, Sticker, X } from "lucide-react";
import {
  uploadLobbyAttachment,
  type LobbyAttachmentRef,
  type LobbyEvent,
} from "../../api";
import { useRoomSocket } from "../../RoomSocketContext";
import { RoomSocketSayError } from "../../roomSocketClient";
import {
  MAX_ATTACHMENTS_MESSAGE,
  MAX_ATTACHMENTS_PER_EVENT,
  lobbySubmitFailureDraft,
  lobbySubmitSuccessDraft,
  selectLobbyAttachmentFiles,
} from "../../lib/lobbyComposerModel";
import { isUnauthorizedApiError } from "../../lib/apiErrors";
import type { RoomPostingMode } from "../../lib/roomGuestPosting";
import type { Mentionable } from "../../lib/mentionComposerModel";
import { parseVoteCommand } from "../../lib/votePoll";
import MentionInput from "./MentionInput";
import VoteComposerDialog, {
  type VoteComposerValue,
} from "./VoteComposerDialog";

type ComposerAccessory = {
  id: "gift" | "gif" | "sticker" | "apps";
  label: string;
  title: string;
  notice: string;
  insertText?: string;
  icon?: LucideIcon;
};

const COMPOSER_ACCESSORIES: ComposerAccessory[] = [
  {
    id: "gift",
    label: "선물",
    title: "선물",
    notice: "선물 기능은 외부 Discord로 전송하지 않습니다. 로컬 메시지에 선물 설명을 남길 수 있습니다.",
    insertText: "[선물: ]",
    icon: Gift,
  },
  {
    id: "gif",
    label: "GIF",
    title: "GIF",
    notice: "GIF 검색은 외부 Discord로 전송하지 않습니다. 로컬 메시지에 GIF 설명을 남길 수 있습니다.",
    insertText: "[GIF: ]",
  },
  {
    id: "sticker",
    label: "스티커",
    title: "스티커",
    notice: "스티커는 외부 Discord로 전송하지 않습니다. 로컬 메시지에 스티커 설명을 남길 수 있습니다.",
    insertText: "[스티커: ]",
    icon: Sticker,
  },
  {
    id: "apps",
    label: "앱",
    title: "앱",
    notice: "앱 명령은 외부 Discord로 전송하지 않습니다. AgentsAssemble 로컬 기능만 이 방에서 다룹니다.",
    insertText: "/",
    icon: Sparkles,
  },
];

export default function LobbyComposer({
  meetingId,
  onPosted,
  submitMessage,
  mentionables = [],
  disabledReason,
  roomSessionToken = "",
  postingMode = "host",
  onGuestSessionExpired,
}: {
  meetingId: string;
  onPosted: (events: LobbyEvent[]) => void;
  submitMessage?: (message: string) => Promise<LobbyEvent[]>;
  mentionables?: Mentionable[];
  disabledReason?: string;
  roomSessionToken?: string;
  postingMode?: RoomPostingMode;
  onGuestSessionExpired?: () => void;
}) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const [message, setMessage] = useState("");
  const [pendingAttachments, setPendingAttachments] = useState<LobbyAttachmentRef[]>([]);
  const [busy, setBusy] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");
  const [accessoryNotice, setAccessoryNotice] = useState("");
  const [voteDialogOpen, setVoteDialogOpen] = useState(false);
  const roomSocket = useRoomSocket();
  const disabled = Boolean(disabledReason);
  const canUploadAttachments =
    postingMode === "host" ||
    (postingMode === "guest" && Boolean(roomSessionToken.trim()));
  const canSubmit = Boolean(message.trim() || pendingAttachments.length) && !busy && !uploading && !disabled;
  const closeVoteDialog = useCallback(() => setVoteDialogOpen(false), []);

  useEffect(() => {
    setVoteDialogOpen(false);
  }, [meetingId]);

  function insertText(text: string) {
    const input = inputRef.current;
    const start = input?.selectionStart ?? message.length;
    const end = input?.selectionEnd ?? message.length;
    const next = `${message.slice(0, start)}${text}${message.slice(end)}`;
    setMessage(next);
    window.setTimeout(() => {
      inputRef.current?.focus();
      inputRef.current?.setSelectionRange(start + text.length, start + text.length);
    }, 0);
  }

  function handleAccessoryClick(accessory: ComposerAccessory) {
    if (disabled || busy) return;
    setError("");
    setAccessoryNotice(accessory.notice);
    if (accessory.insertText) insertText(accessory.insertText);
  }

  async function handleFileChange(event: ChangeEvent<HTMLInputElement>) {
    if (disabled || !canUploadAttachments) return;
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
        uploaded.push(
          await uploadLobbyAttachment(file, {
            roomId: meetingId,
            sessionToken: roomSessionToken,
          })
        );
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
    if (disabled || busy || uploading) return;
    setPendingAttachments((current) =>
      current.filter((attachment) => attachment.id !== attachmentId)
    );
  }

  async function handleSubmit() {
    if (disabled || busy || uploading) return;
    const draftMessage = message;
    const draftAttachments = pendingAttachments;
    const trimmed = draftMessage.trim();
    if (!trimmed && draftAttachments.length === 0) return;
    if (trimmed.toLocaleLowerCase() === "/vote") {
      setError("");
      setVoteDialogOpen(true);
      return;
    }

    setBusy(true);
    setError("");
    try {
      if (postingMode === "guest" && !roomSessionToken) {
        throw new Error("메시지를 보내려면 유효한 초대 세션이 필요합니다.");
      }
      // "/vote 질문 | 옵션1 | 옵션2" opens a poll card instead of a message.
      const voteCommand = parseVoteCommand(trimmed);
      const sayRequest = {
        message: voteCommand ? "" : trimmed,
        attachments: draftAttachments,
        kind: voteCommand ? ("vote" as const) : ("message" as const),
        voteQuestion: voteCommand?.question || "",
        voteOptions: voteCommand?.options || [],
      };
      const payload =
        submitMessage && sayRequest.kind === "message" && sayRequest.attachments.length === 0
          ? { events: await submitMessage(sayRequest.message) }
          : roomSocket?.ready()
            ? await roomSocket.say(sayRequest)
            : await Promise.reject(
                new RoomSocketSayError(
                  "방 연결이 준비되지 않았습니다. 연결된 뒤 다시 보내 주세요.",
                  "socket_not_ready"
                )
              );
      const cleared = lobbySubmitSuccessDraft<LobbyAttachmentRef>();
      setMessage(cleared.message);
      setPendingAttachments(cleared.pendingAttachments);
      onPosted(payload.events || (payload.event ? [payload.event] : []));
    } catch (errorValue) {
      if (
        isUnauthorizedApiError(errorValue) ||
        (errorValue instanceof RoomSocketSayError && errorValue.category === "session_revoked")
      ) {
        onGuestSessionExpired?.();
      }
      const restored = lobbySubmitFailureDraft(
        draftMessage,
        draftAttachments,
        errorValue instanceof Error ? errorValue.message : "채팅 메시지 전송 실패"
      );
      setMessage(restored.message);
      setPendingAttachments(restored.pendingAttachments);
      setError(restored.error);
    } finally {
      setBusy(false);
    }
  }

  async function submitVote(value: VoteComposerValue) {
    if (disabled || busy || uploading) {
      throw new Error("지금은 투표를 만들 수 없습니다.");
    }
    setBusy(true);
    setError("");
    try {
      if (postingMode === "guest" && !roomSessionToken) {
        throw new Error("투표를 만들려면 유효한 초대 세션이 필요합니다.");
      }
      if (!roomSocket?.ready()) {
        throw new RoomSocketSayError(
          "방 연결이 준비되지 않았습니다. 연결된 뒤 다시 보내 주세요.",
          "socket_not_ready"
        );
      }
      const payload = await roomSocket.say({
        message: "",
        attachments: pendingAttachments,
        kind: "vote",
        voteQuestion: value.question,
        voteOptions: value.options,
        voteDurationSeconds: value.durationSeconds,
      });
      const cleared = lobbySubmitSuccessDraft<LobbyAttachmentRef>();
      setMessage(cleared.message);
      setPendingAttachments(cleared.pendingAttachments);
      onPosted(payload.events || (payload.event ? [payload.event] : []));
    } catch (errorValue) {
      if (
        isUnauthorizedApiError(errorValue) ||
        (errorValue instanceof RoomSocketSayError &&
          errorValue.category === "session_revoked")
      ) {
        onGuestSessionExpired?.();
      }
      throw errorValue;
    } finally {
      setBusy(false);
    }
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== "Enter" || event.nativeEvent.isComposing) return;
    if (event.shiftKey) return;
    event.preventDefault();
    void handleSubmit();
  }

  return (
    <>
      <section className="dc-composer-shell">
      {error && (
        <p className="mb-2 rounded border border-danger/30 bg-danger/10 px-3 py-2 text-[12px] font-semibold text-danger preserve-words">
          {error}
        </p>
      )}
      {disabledReason && (
        <p className="dc-composer-readonly preserve-words">
          {disabledReason}
        </p>
      )}
      {accessoryNotice && !disabledReason && (
        <p className="dc-composer-accessory-notice preserve-words" aria-live="polite">
          {accessoryNotice}
        </p>
      )}

      {pendingAttachments.length > 0 && (
        <div className="mb-2 flex flex-wrap gap-2">
          {pendingAttachments.map((attachment) => (
            <span
              key={attachment.id}
              className="dc-composer-attachment inline-flex max-w-full items-center gap-2 px-3 py-1.5 text-[12px] font-bold text-text-secondary"
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

      <div className="dc-composer-bar">
        <MentionInput
          inputRef={inputRef}
          value={message}
          onChange={setMessage}
          onKeyDown={handleKeyDown}
          className="dc-composer-input"
          placeholder={disabledReason || (uploading ? "첨부 업로드 중..." : "이 방에 메시지 남기기...")}
          disabled={busy || disabled}
          mentionables={mentionables}
          ariaLabel="채팅 입력"
        />
        <input
          ref={fileInputRef}
          type="file"
          multiple
          className="hidden"
          onChange={handleFileChange}
          aria-label="채팅 첨부 선택"
        />
        <button
          type="button"
          onClick={() => fileInputRef.current?.click()}
          disabled={
            disabled ||
            !canUploadAttachments ||
            busy ||
            uploading ||
            pendingAttachments.length >= MAX_ATTACHMENTS_PER_EVENT
          }
          className="dc-composer-button"
          data-role="attachment"
          aria-label="첨부 추가"
          title={`첨부 ${pendingAttachments.length}/${MAX_ATTACHMENTS_PER_EVENT}`}
        >
          <Paperclip size={17} />
        </button>
        {COMPOSER_ACCESSORIES.map((accessory) => {
          const Icon = accessory.icon;
          return (
            <button
              key={accessory.id}
              type="button"
              onClick={() => handleAccessoryClick(accessory)}
              disabled={busy || disabled}
              className="dc-composer-button"
              data-accessory={accessory.id}
              aria-label={`채팅 ${accessory.label}`}
              title={accessory.title}
            >
              {Icon ? <Icon size={17} /> : <span className="dc-composer-button-label">{accessory.label}</span>}
            </button>
          );
        })}
        <button
          type="button"
          onClick={() => insertText("@")}
          disabled={busy || disabled}
          className="dc-composer-button"
          data-role="mention"
          aria-label="멘션 삽입"
          title="@멘션"
        >
          <AtSign size={17} />
        </button>
        <button
          type="button"
          onClick={() => insertText("🙂")}
          disabled={busy || disabled}
          className="dc-composer-button"
          data-role="emoji"
          aria-label="이모지 삽입"
          title="이모지"
        >
          <Smile size={17} />
        </button>
        <button
          type="button"
          onClick={handleSubmit}
          disabled={!canSubmit}
          className="dc-composer-button send"
          data-role="send"
          aria-label="채팅 메시지 보내기"
        >
          <Send size={17} />
        </button>
      </div>
      </section>
      {voteDialogOpen && (
        <VoteComposerDialog
          onClose={closeVoteDialog}
          onSubmit={submitVote}
        />
      )}
    </>
  );
}
