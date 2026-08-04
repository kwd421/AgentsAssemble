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
import { Bot, UserRound } from "lucide-react";
import {
  insertMentionText,
  mentionOptions,
  mentionQueryAtCursor,
  type Mentionable,
} from "../../lib/mentionComposerModel";
import ProviderLogo from "./ProviderLogo";
import "./MentionInput.css";

type MentionInputProps = {
  value: string;
  onChange: (value: string) => void;
  mentionables?: Mentionable[];
  inputRef?: RefObject<HTMLTextAreaElement | null>;
  onKeyDown?: (event: KeyboardEvent<HTMLTextAreaElement>) => void;
  className?: string;
  placeholder?: string;
  disabled?: boolean;
  maxLength?: number;
  ariaLabel?: string;
  externalListId?: string;
  externalActiveOptionId?: string;
  externalListOpen?: boolean;
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
  externalListId,
  externalActiveOptionId,
  externalListOpen = false,
}: MentionInputProps) {
  const internalRef = useRef<HTMLTextAreaElement>(null);
  const targetRef = inputRef || internalRef;
  const mentionListId = useId();
  const [activeOptionIndex, setActiveOptionIndex] = useState(0);
  const [mentionCursor, setMentionCursor] = useState(value.length);
  const [dismissedMentionKey, setDismissedMentionKey] = useState("");
  const [suppressMentionSuggestions, setSuppressMentionSuggestions] = useState(false);
  const mentionMatch = useMemo(
    () => mentionQueryAtCursor(value, mentionCursor),
    [mentionCursor, value]
  );
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
  const controlledListId =
    options.length > 0 ? mentionListId : externalListOpen ? externalListId : undefined;
  const controlledOptionId =
    options.length > 0 ? activeOptionId : externalListOpen ? externalActiveOptionId : undefined;

  useEffect(() => {
    setActiveOptionIndex(0);
  }, [mentionQueryKey]);

  useEffect(() => {
    setMentionCursor((current) => Math.min(current, value.length));
  }, [value.length]);

  useEffect(() => {
    setActiveOptionIndex((current) => {
      if (options.length === 0) return 0;
      return Math.min(current, options.length - 1);
    });
  }, [options.length]);

  function syncMentionCursor() {
    setMentionCursor(targetRef.current?.selectionStart ?? value.length);
  }

  function chooseMention(mentionable: Mentionable) {
    const cursor = targetRef.current?.selectionStart ?? value.length;
    const query = mentionQueryAtCursor(value, cursor);
    const next = insertMentionText(value, cursor, query, mentionable);
    setDismissedMentionKey("");
    setSuppressMentionSuggestions(true);
    setMentionCursor(next.cursor);
    onChange(next.message);
    window.setTimeout(() => {
      targetRef.current?.focus();
      targetRef.current?.setSelectionRange(next.cursor, next.cursor);
      setMentionCursor(next.cursor);
    }, 0);
  }

  function handleInputChange(event: ChangeEvent<HTMLTextAreaElement>) {
    setDismissedMentionKey("");
    setSuppressMentionSuggestions(false);
    setMentionCursor(event.target.selectionStart ?? event.target.value.length);
    onChange(event.target.value);
  }

  function handleMentionKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
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

    if (event.key === "Enter" || event.key === "Tab") {
      event.preventDefault();
      const option = options[activeOptionIndex] || options[0];
      if (option) chooseMention(option);
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
          {options.map((option, index) => (
            <button
              key={option.token}
              id={`${mentionListId}-option-${index}`}
              type="button"
              onMouseDown={(event) => event.preventDefault()}
              onMouseEnter={() => setActiveOptionIndex(index)}
              onClick={() => chooseMention(option)}
              role="option"
              aria-selected={index === activeOptionIndex}
              aria-label={
                option.detail ? `${option.label}, ${option.detail}` : option.label
              }
            >
              <span
                className="dc-mention-avatar"
                data-participant-kind={option.participantKind || "unknown"}
              >
                {option.avatarImage ? (
                  <img src={option.avatarImage} alt="" />
                ) : option.participantKind === "agent" ? (
                  <ProviderLogo
                    providerKind={option.providerKind}
                    size={32}
                    fallback={<Bot size={16} />}
                  />
                ) : (
                  <UserRound size={17} />
                )}
              </span>
              <span className="dc-mention-copy">
                <strong>{option.label}</strong>
                {option.detail && <small>{option.detail}</small>}
              </span>
            </button>
          ))}
        </div>
      )}
      <textarea
        ref={targetRef}
        value={value}
        onChange={handleInputChange}
        onKeyDown={handleMentionKeyDown}
        onKeyUp={syncMentionCursor}
        onClick={syncMentionCursor}
        onSelect={syncMentionCursor}
        className={className}
        placeholder={placeholder}
        disabled={disabled}
        maxLength={maxLength}
        aria-label={ariaLabel}
        aria-autocomplete="list"
        aria-controls={controlledListId}
        aria-expanded={options.length > 0 || externalListOpen}
        aria-activedescendant={controlledOptionId}
        rows={1}
      />
    </>
  );
}
