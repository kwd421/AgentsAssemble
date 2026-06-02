import { useMemo, useRef, type KeyboardEvent, type RefObject } from "react";
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
  const mentionMatch = useMemo(() => {
    const selectionStart = targetRef.current?.selectionStart ?? value.length;
    return mentionQueryAtCursor(value, selectionStart);
  }, [targetRef, value]);
  const options = useMemo(
    () => mentionOptions(mentionables, mentionMatch),
    [mentionMatch, mentionables]
  );

  function chooseMention(name: string) {
    const cursor = targetRef.current?.selectionStart ?? value.length;
    const query = mentionQueryAtCursor(value, cursor);
    const next = insertMentionText(value, cursor, query, name);
    onChange(next.message);
    window.setTimeout(() => {
      targetRef.current?.focus();
      targetRef.current?.setSelectionRange(next.cursor, next.cursor);
    }, 0);
  }

  return (
    <>
      {options.length > 0 && (
        <div className="dc-mention-popover" role="listbox" aria-label="멘션 후보">
          {options.map((name) => (
            <button
              key={name}
              type="button"
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => chooseMention(name)}
              role="option"
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
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={onKeyDown}
        className={className}
        placeholder={placeholder}
        disabled={disabled}
        maxLength={maxLength}
        aria-label={ariaLabel}
      />
    </>
  );
}
