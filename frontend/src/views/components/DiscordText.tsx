import type { ReactNode } from "react";
import { tokenizeDiscordText, type DiscordTextToken } from "../../lib/discordTextTokens";

function previewTitleForUrl(url: URL) {
  const hostname = url.hostname.replace(/^www\./i, "");
  const pathPart = url.pathname
    .split("/")
    .filter(Boolean)
    .at(-1)
    ?.replace(/[-_]+/g, " ")
    .trim();
  return pathPart || hostname;
}

function linkPreviewsForText(text: string) {
  const seen = new Set<string>();
  return tokenizeDiscordText(text)
    .filter((token) => token.kind === "link")
    .map((token) => token.value)
    .filter((value) => {
      if (seen.has(value)) return false;
      seen.add(value);
      return true;
    })
    .map((value) => {
      try {
        const url = new URL(value);
        return {
          url: value,
          host: url.hostname.replace(/^www\./i, ""),
          title: previewTitleForUrl(url),
        };
      } catch {
        return null;
      }
    })
    .filter((preview): preview is { url: string; host: string; title: string } => Boolean(preview));
}

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
  const previews = linkPreviewsForText(text);
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
      {previews.length > 0 && (
        <div className="dc-link-preview-list">
          {previews.map((preview) => (
            <a
              key={preview.url}
              className="dc-link-preview"
              href={preview.url}
              target="_blank"
              rel="noreferrer"
            >
              <span className="dc-link-preview-host">{preview.host}</span>
              <span className="dc-link-preview-title">{preview.title}</span>
              <span className="dc-link-preview-url">{preview.url}</span>
            </a>
          ))}
        </div>
      )}
    </>
  );
}
