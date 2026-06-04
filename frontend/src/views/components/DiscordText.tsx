import type { ReactNode } from "react";
import { tokenizeDiscordText, type DiscordTextToken } from "../../lib/discordTextTokens";

function renderToken(token: DiscordTextToken, key: string): ReactNode {
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
  if (token.kind === "link") {
    return (
      <a key={key} className="dc-chat-link" href={token.value} target="_blank" rel="noreferrer">
        {token.value}
      </a>
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
          {tokenizeDiscordText(line).map((token, tokenIndex) =>
            renderToken(token, `${lineIndex}:${tokenIndex}`)
          )}
          {lineIndex < lines.length - 1 && <br />}
        </span>
      ))}
    </>
  );
}
