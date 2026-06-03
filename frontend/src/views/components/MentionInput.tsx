import {
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type ChangeEvent,
  type KeyboardEvent,
  type RefObject,
} from "react";
import {
  insertMentionText,
  mentionOptions,
  mentionQueryAtCursor,
} from "../../lib/mentionComposerModel";

type MentionInputProps = {
  value: string;
  onChange: (value: string) => void;
  mentionables?: string[];
  inputRef?: RefObject<HTMLInputElement | null>;
  onKeyDown?: (event: KeyboardEvent<HTMLInputElement>) => void;
  className?: string;
  placeholder?: string;
  disabled?: boolean;
  maxLength?: number;
  ariaLabel?: string;
};

export default function MentionInput({
  value,
  onChange,
  mentionables = [],
  inputRef,
  onKeyDown,
  className,
  placeholder,
  disabled,
  maxLength,
  ariaLabel,
}: MentionInputProps) {
  const internalRef = useRef<HTMLInputElement>(null);
  const targetRef = inputRef || internalRef;
  const mentionListId = useId();
  const [activeOptionIndex, setActiveOptionIndex] = useState(0);
  const [dismissedMentionKey, setDismissedMentionKey] = useState("");
  const [suppressMentionSuggestions, setSuppressMentionSuggestions] = useState(false);
  const mentionMatch = useMemo(() => {
    const selectionStart = targetRef.current?.selectionStart ?? value.length;
    return mentionQueryAtCursor(value, selectionStart);
  }, [targetRef, value]);
  const mentionQueryKey = mentionMatch ? `${mentionMatch.start}:${mentionMatch.query}` : "";
  const options = useMemo(
    () =>
      suppressMentionSuggestions || (mentionQueryKey && dismissedMentionKey === mentionQueryKey)
        ? []
        : mentionOptions(mentionables, mentionMatch),
    [dismissedMentionKey, mentionMatch, mentionQueryKey, mentionables, suppressMentionSuggestions]
  );
  const activeOptionId =
    options.length > 0 ? `${mentionListId}-option-${activeOptionIndex}` : undefined;

  useEffect(() => {
    setActiveOptionIndex(0);
  }, [mentionQueryKey]);

  useEffect(() => {
    setActiveOptionIndex((current) => {
      if (options.length === 0) return 0;
      return Math.min(current, options.length - 1);
    });
  }, [options.length]);

  function chooseMention(name: string) {
    const cursor = targetRef.current?.selectionStart ?? value.length;
    const query = mentionQueryAtCursor(value, cursor);
    const next = insertMentionText(value, cursor, query, name);
    setDismissedMentionKey("");
    setSuppressMentionSuggestions(true);
    onChange(next.message);
    window.setTimeout(() => {
      targetRef.current?.focus();
      targetRef.current?.setSelectionRange(next.cursor, next.cursor);
    }, 0);
  }

  function handleInputChange(event: ChangeEvent<HTMLInputElement>) {
    setDismissedMentionKey("");
    setSuppressMentionSuggestions(false);
    onChange(event.target.value);
  }

  function handleMentionKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (options.length === 0) {
      onKeyDown?.(event);
      return;
    }

    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActiveOptionIndex((current) => (current + 1) % options.length);
      return;
    }

    if (event.key === "ArrowUp") {
      event.preventDefault();
      setActiveOptionIndex((current) => (current - 1 + options.length) % options.length);
      return;
    }

    if (event.key === "Enter") {
      event.preventDefault();
      chooseMention(options[activeOptionIndex] || options[0]);
      return;
    }

    if (event.key === "Escape") {
      event.preventDefault();
      setDismissedMentionKey(mentionQueryKey);
      return;
    }

    onKeyDown?.(event);
  }

  return (
    <>
      {options.length > 0 && (
        <div
          id={mentionListId}
          className="dc-mention-popover"
          role="listbox"
          aria-label="멘션 후보"
        >
          {options.map((name, index) => (
            <button
              key={name}
              id={`${mentionListId}-option-${index}`}
              type="button"
              onMouseDown={(event) => event.preventDefault()}
              onMouseEnter={() => setActiveOptionIndex(index)}
              onClick={() => chooseMention(name)}
              role="option"
              aria-selected={index === activeOptionIndex}
            >
              <span className="dc-mention-avatar">@</span>
              <span>{name}</span>
            </button>
          ))}
        </div>
      )}
      <input
        ref={targetRef}
        value={value}
        onChange={handleInputChange}
        onKeyDown={handleMentionKeyDown}
        className={className}
        placeholder={placeholder}
        disabled={disabled}
        maxLength={maxLength}
        aria-label={ariaLabel}
        aria-autocomplete="list"
        aria-controls={options.length > 0 ? mentionListId : undefined}
        aria-expanded={options.length > 0}
        aria-activedescendant={activeOptionId}
      />
    </>
  );
}
