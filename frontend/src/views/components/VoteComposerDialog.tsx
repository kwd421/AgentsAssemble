import {
  useEffect,
  useId,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent as ReactKeyboardEvent,
} from "react";
import { Plus, Trash2, X } from "lucide-react";

const MIN_OPTIONS = 2;
const MAX_OPTIONS = 10;
const DEFAULT_DURATION_MINUTES = 5;
const FOCUSABLE_SELECTOR = [
  "button:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "a[href]",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

export type VoteComposerValue = {
  question: string;
  options: string[];
  durationSeconds: number;
};

export default function VoteComposerDialog({
  onClose,
  onSubmit,
}: {
  onClose: () => void;
  onSubmit: (value: VoteComposerValue) => Promise<void>;
}) {
  const titleId = useId();
  const durationHelpId = useId();
  const dialogRef = useRef<HTMLElement>(null);
  const questionInputRef = useRef<HTMLInputElement>(null);
  const busyRef = useRef(false);
  const [question, setQuestion] = useState("");
  const [options, setOptions] = useState(["", ""]);
  const [durationMinutes, setDurationMinutes] = useState(DEFAULT_DURATION_MINUTES);
  const [noDeadline, setNoDeadline] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    busyRef.current = busy;
  }, [busy]);

  useEffect(() => {
    const previouslyFocused = document.activeElement as HTMLElement | null;
    questionInputRef.current?.focus();

    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === "Escape" && !busyRef.current) onClose();
    }
    window.addEventListener("keydown", closeOnEscape);
    return () => {
      window.removeEventListener("keydown", closeOnEscape);
      if (previouslyFocused?.isConnected) previouslyFocused.focus();
    };
  }, [onClose]);

  function containFocus(event: ReactKeyboardEvent<HTMLElement>) {
    if (event.key !== "Tab") return;
    const focusable = Array.from(
      dialogRef.current?.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR) || []
    );
    if (!focusable.length) {
      event.preventDefault();
      dialogRef.current?.focus();
      return;
    }
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  function updateOption(index: number, value: string) {
    setOptions((current) =>
      current.map((option, optionIndex) =>
        optionIndex === index ? value : option
      )
    );
  }

  function removeOption(index: number) {
    if (options.length <= MIN_OPTIONS || busy) return;
    setOptions((current) =>
      current.filter((_option, optionIndex) => optionIndex !== index)
    );
  }

  function addOption() {
    if (options.length >= MAX_OPTIONS || busy) return;
    setOptions((current) => [...current, ""]);
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    const trimmedQuestion = question.trim();
    const trimmedOptions = options.map((option) => option.trim());
    if (!trimmedQuestion) {
      setError("투표 질문을 입력해 주세요.");
      return;
    }
    if (trimmedOptions.some((option) => !option)) {
      setError("모든 선택지에 이름을 입력해 주세요.");
      return;
    }
    if (
      new Set(trimmedOptions.map((option) => option.toLocaleLowerCase())).size !==
      trimmedOptions.length
    ) {
      setError("선택지 이름은 서로 달라야 합니다.");
      return;
    }
    if (
      !noDeadline &&
      (!Number.isInteger(durationMinutes) ||
        durationMinutes < 1 ||
        durationMinutes > 1440)
    ) {
      setError("투표 기간은 1분에서 1440분 사이여야 합니다.");
      return;
    }

    setBusy(true);
    setError("");
    try {
      await onSubmit({
        question: trimmedQuestion,
        options: trimmedOptions,
        durationSeconds: noDeadline ? 0 : durationMinutes * 60,
      });
      onClose();
    } catch (errorValue) {
      setError(
        errorValue instanceof Error
          ? errorValue.message
          : "투표를 만들지 못했습니다."
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
        ref={dialogRef}
        className="dc-create-channel-modal max-h-[calc(100vh-48px)] overflow-y-auto"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        onMouseDown={(event) => event.stopPropagation()}
        onKeyDown={containFocus}
      >
        <header className="dc-create-channel-head">
          <h2 id={titleId}>투표 만들기</h2>
          <button
            type="button"
            className="dc-settings-close"
            onClick={onClose}
            disabled={busy}
            aria-label="투표 만들기 닫기"
          >
            <X size={18} />
          </button>
        </header>

        <form className="grid gap-4" onSubmit={submit}>
          <label className="dc-create-channel-name">
            질문
            <input
              ref={questionInputRef}
              className="ops-input"
              value={question}
              maxLength={300}
              placeholder="어느 길로 갈까요?"
              onChange={(event) => setQuestion(event.target.value)}
              disabled={busy}
            />
          </label>

          <fieldset className="grid gap-2">
            <legend className="mb-1 font-extrabold">선택지</legend>
            {options.map((option, index) => (
              <div
                key={index}
                className="grid grid-cols-[minmax(0,1fr)_36px] gap-2"
              >
                <input
                  className="ops-input"
                  value={option}
                  maxLength={100}
                  placeholder={`선택지 ${index + 1}`}
                  onChange={(event) => updateOption(index, event.target.value)}
                  disabled={busy}
                  aria-label={`선택지 ${index + 1}`}
                />
                <button
                  type="button"
                  className="ops-button grid place-items-center px-0"
                  onClick={() => removeOption(index)}
                  disabled={busy || options.length <= MIN_OPTIONS}
                  aria-label={`선택지 ${index + 1} 제거`}
                >
                  <Trash2 size={15} />
                </button>
              </div>
            ))}
            <button
              type="button"
              className="ops-button mt-1 inline-flex items-center justify-center gap-2"
              onClick={addOption}
              disabled={busy || options.length >= MAX_OPTIONS}
            >
              <Plus size={15} />
              선택지 추가
            </button>
          </fieldset>

          <div className="grid gap-2">
            <label className="dc-create-channel-name">
              투표 기간 (분)
              <input
                className="ops-input"
                type="number"
                min={1}
                max={1440}
                step={1}
                value={durationMinutes}
                onChange={(event) => {
                  const nextValue = event.currentTarget.valueAsNumber;
                  setDurationMinutes(Number.isFinite(nextValue) ? nextValue : 0);
                }}
                disabled={busy || noDeadline}
                aria-label="투표 기간 (분)"
                aria-describedby={durationHelpId}
              />
            </label>
            <label className="inline-flex items-center gap-2 text-sm font-semibold">
              <input
                type="checkbox"
                checked={noDeadline}
                onChange={(event) => setNoDeadline(event.currentTarget.checked)}
                disabled={busy}
              />
              마감 시간 없음
            </label>
            <span
              id={durationHelpId}
              className="text-[12px] font-medium text-text-muted"
            >
              {noDeadline
                ? "투표를 만든 사람이나 방 관리자/호스트가 직접 종료할 때까지 열립니다."
                : "설정한 시간이 지나면 서버가 새 투표를 받지 않습니다."}
            </span>
          </div>

          {error && (
            <p
              className="dc-channel-composer-error preserve-words"
              role="alert"
            >
              {error}
            </p>
          )}

          <div className="dc-create-channel-actions">
            <button
              type="button"
              className="ops-button min-w-[58px] px-3 py-2"
              onClick={onClose}
              disabled={busy}
            >
              취소
            </button>
            <button
              type="submit"
              className="ops-cta min-w-[58px] px-3 py-2"
              disabled={busy}
            >
              {busy ? "저장 중…" : "만들기"}
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}
