import type { ReactNode } from "react";

type TokenKind = "text" | "mention" | "channel" | "code" | "bold" | "italic" | "strike";

type Token = {
  kind: TokenKind;
  value: string;
};

const INLINE_PATTERN =
  /(`[^`]+`|\*\*[^*]+\*\*|~~[^~]+~~|\*[^*]+\*|@[^\s@#:`*~]+|#[^\s@#:`*~]+)/gu;

function trimWrapper(value: string, wrapper: string) {
  return value.slice(wrapper.length, value.length - wrapper.length);
}

function classifyInline(value: string): Token {
  if (value.startsWith("@")) return { kind: "mention", value };
  if (value.startsWith("#")) return { kind: "channel", value };
  if (value.startsWith("`") && value.endsWith("`")) {
    return { kind: "code", value: trimWrapper(value, "`") };
  }
  if (value.startsWith("**") && value.endsWith("**")) {
    return { kind: "bold", value: trimWrapper(value, "**") };
  }
  if (value.startsWith("~~") && value.endsWith("~~")) {
    return { kind: "strike", value: trimWrapper(value, "~~") };
  }
  if (value.startsWith("*") && value.endsWith("*")) {
    return { kind: "italic", value: trimWrapper(value, "*") };
  }
  return { kind: "text", value };
}

function tokenizeInline(text: string): Token[] {
  const tokens: Token[] = [];
  let cursor = 0;
  for (const match of text.matchAll(INLINE_PATTERN)) {
    const index = match.index ?? 0;
    if (index > cursor) {
      tokens.push({ kind: "text", value: text.slice(cursor, index) });
    }
    tokens.push(classifyInline(match[0]));
    cursor = index + match[0].length;
  }
  if (cursor < text.length) {
    tokens.push({ kind: "text", value: text.slice(cursor) });
  }
  return tokens;
}

function renderToken(token: Token, key: string): ReactNode {
  if (token.kind === "mention") {
    return (
      <span key={key} className="dc-mention">
        {token.value}
      </span>
    );
  }
  if (token.kind === "channel") {
    return (
      <span key={key} className="dc-channel-mention">
        {token.value}
      </span>
    );
  }
  if (token.kind === "code") {
    return (
      <code key={key} className="dc-inline-code">
        {token.value}
      </code>
    );
  }
  if (token.kind === "bold") return <strong key={key}>{token.value}</strong>;
  if (token.kind === "italic") return <em key={key}>{token.value}</em>;
  if (token.kind === "strike") return <s key={key}>{token.value}</s>;
  return token.value;
}

export default function DiscordText({ text }: { text: string }) {
  const lines = text.split(/\r?\n/);
  return (
    <>
      {lines.map((line, lineIndex) => (
        <span key={lineIndex}>
          {tokenizeInline(line).map((token, tokenIndex) =>
            renderToken(token, `${lineIndex}:${tokenIndex}`)
          )}
          {lineIndex < lines.length - 1 && <br />}
        </span>
      ))}
    </>
  );
}
