import { useEffect, useState } from "react";
import { Hash, Volume2, X } from "lucide-react";

/**
 * Discord-style "create channel" modal: pick a type (text/voice) and a name.
 * The shell owns the actual create call so it can refresh the channel list and
 * route to the new channel.
 */
export default function CreateChannelModal({
  onClose,
  onCreate,
}: {
  onClose: () => void;
  onCreate: (params: { name: string; type: "text" | "voice" }) => Promise<void>;
}) {
  const [name, setName] = useState("");
  const [type, setType] = useState<"text" | "voice">("text");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    function onEscape(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onEscape);
    return () => window.removeEventListener("keydown", onEscape);
  }, [onClose]);

  async function submit() {
    const trimmed = name.trim();
    if (!trimmed || busy) return;
    setBusy(true);
    setError("");
    try {
      await onCreate({ name: trimmed, type });
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "채널을 만들지 못했습니다");
      setBusy(false);
    }
  }

  return (
    <div className="dc-settings-backdrop" role="presentation" onClick={onClose}>
      <section
        className="dc-create-channel-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="create-channel-title"
        onClick={(event) => event.stopPropagation()}
      >
        <header className="dc-create-channel-head">
          <h2 id="create-channel-title">채널 만들기</h2>
          <button type="button" className="dc-settings-close" onClick={onClose} aria-label="닫기">
            <X size={18} />
          </button>
        </header>
        <div className="dc-radio-stack">
          <label>
            <input type="radio" name="channel-type" checked={type === "text"} onChange={() => setType("text")} />
            <Hash size={16} />
            <span>텍스트 — 메시지를 주고받는 채널</span>
          </label>
          <label>
            <input type="radio" name="channel-type" checked={type === "voice"} onChange={() => setType("voice")} />
            <Volume2 size={16} />
            <span>음성 — 모여서 통화하는 채널 (오디오는 준비 중)</span>
          </label>
        </div>
        <label className="dc-create-channel-name">
          채널 이름
          <input
            className="ops-input"
            autoFocus
            value={name}
            maxLength={60}
            placeholder={type === "voice" ? "음성 라운지" : "구현방"}
            onChange={(event) => setName(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                void submit();
              }
            }}
          />
        </label>
        {error && <p className="dc-channel-composer-error preserve-words">{error}</p>}
        <div className="dc-create-channel-actions">
          <button type="button" className="ops-button" onClick={onClose}>
            취소
          </button>
          <button type="button" className="ops-cta" disabled={busy || !name.trim()} onClick={() => void submit()}>
            만들기
          </button>
        </div>
      </section>
    </div>
  );
}
