export type DiscordTextTokenKind =
  | "text"
  | "mention"
  | "channel"
  | "link"
  | "code"
  | "bold"
  | "italic"
  | "strike";

export type DiscordTextToken = {
  kind: DiscordTextTokenKind;
  value: string;
};

const INLINE_PATTERN =
  /(https?:\/\/[^\s<>"'`*~]+|<@[^>\r\n]{1,80}>|`[^`]+`|\*\*[^*]+\*\*|~~[^~]+~~|\*[^*]+\*|@[^\s@#:`*~<>]+|#[^\s@#:`*~<>]+)/gu;

const TRAILING_LINK_SENTENCE_PUNCTUATION = new Set([
  ".",
  ",",
  "!",
  "?",
  ";",
  ":",
  "。",
  "，",
  "！",
  "？",
  "；",
  "：",
]);

const TRAILING_LINK_BRACKETS: Record<string, string> = {
  ")": "(",
  "]": "[",
  "}": "{",
};

function trimWrapper(value: string, wrapper: string) {
  return value.slice(wrapper.length, value.length - wrapper.length);
}

function countCharacters(value: string, character: string) {
  let count = 0;
  for (const item of value) {
    if (item === character) count += 1;
  }
  return count;
}

function hasUnmatchedTrailingBracket(value: string, closing: string) {
  const opening = TRAILING_LINK_BRACKETS[closing];
  return Boolean(opening) && countCharacters(value, closing) > countCharacters(value, opening);
}

function splitLinkToken(value: string): DiscordTextToken[] {
  let link = value;
  let trailing = "";
  while (link.length) {
    const last = link.at(-1) || "";
    const shouldTrim =
      TRAILING_LINK_SENTENCE_PUNCTUATION.has(last) || hasUnmatchedTrailingBracket(link, last);
    if (!shouldTrim) break;
    trailing = last + trailing;
    link = link.slice(0, -last.length);
  }
  if (!link) return [{ kind: "text", value }];
  if (!trailing) return [{ kind: "link", value: link }];
  return [
    { kind: "link", value: link },
    { kind: "text", value: trailing },
  ];
}

function classifyInline(value: string): DiscordTextToken[] {
  if (value.startsWith("<@") && value.endsWith(">")) {
    return [{ kind: "mention", value: `@${value.slice(2, -1)}` }];
  }
  if (value.startsWith("@")) return [{ kind: "mention", value }];
  if (value.startsWith("#")) return [{ kind: "channel", value }];
  if (value.startsWith("http://") || value.startsWith("https://")) {
    return splitLinkToken(value);
  }
  if (value.startsWith("`") && value.endsWith("`")) {
    return [{ kind: "code", value: trimWrapper(value, "`") }];
  }
  if (value.startsWith("**") && value.endsWith("**")) {
    return [{ kind: "bold", value: trimWrapper(value, "**") }];
  }
  if (value.startsWith("~~") && value.endsWith("~~")) {
    return [{ kind: "strike", value: trimWrapper(value, "~~") }];
  }
  if (value.startsWith("*") && value.endsWith("*")) {
    return [{ kind: "italic", value: trimWrapper(value, "*") }];
  }
  return [{ kind: "text", value }];
}

export function tokenizeDiscordText(text: string): DiscordTextToken[] {
  const tokens: DiscordTextToken[] = [];
  let cursor = 0;
  for (const match of text.matchAll(INLINE_PATTERN)) {
    const index = match.index ?? 0;
    if (index > cursor) {
      tokens.push({ kind: "text", value: text.slice(cursor, index) });
    }
    tokens.push(...classifyInline(match[0]));
    cursor = index + match[0].length;
  }
  if (cursor < text.length) {
    tokens.push({ kind: "text", value: text.slice(cursor) });
  }
  return tokens;
}
