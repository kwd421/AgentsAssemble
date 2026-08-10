import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { tokenizeDiscordText } from "../../lib/discordTextTokens";
import { repairMarkdownTables } from "../../lib/repairMarkdownTables";

type HastNode = {
  type: string;
  tagName?: string;
  value?: string;
  properties?: Record<string, unknown>;
  children?: HastNode[];
};

export type MentionLabels = Readonly<Record<string, string>>;

const ROOM_REFERENCE_PATTERN = /(<@[^>\r\n]{1,80}>|@[^\s@#:`*~<>]+|#[^\s@#:`*~<>]+)/gu;

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

function roomReferenceNodes(value: string, mentionLabels: MentionLabels): HastNode[] {
  const nodes: HastNode[] = [];
  let cursor = 0;
  for (const match of value.matchAll(ROOM_REFERENCE_PATTERN)) {
    const index = match.index ?? 0;
    if (index > cursor) nodes.push({ type: "text", value: value.slice(cursor, index) });
    const raw = match[0];
    const structuredMention = raw.startsWith("<@");
    const mention = structuredMention || raw.startsWith("@");
    const mentionToken = structuredMention ? raw.slice(2, -1).trim() : "";
    const mentionLabel = structuredMention
      ? mentionLabels[mentionToken] || mentionToken
      : raw.slice(1);
    nodes.push({
      type: "element",
      tagName: "span",
      properties: { className: [mention ? "dc-mention" : "dc-channel-mention"] },
      children: [{ type: "text", value: mention ? `@${mentionLabel}` : raw }],
    });
    cursor = index + raw.length;
  }
  if (cursor < value.length) nodes.push({ type: "text", value: value.slice(cursor) });
  return nodes.length ? nodes : [{ type: "text", value }];
}

function rehypeRoomReferences({ mentionLabels = {} }: { mentionLabels?: MentionLabels } = {}) {
  return (tree: HastNode) => {
    const visit = (node: HastNode, blocked = false) => {
      const nextBlocked = blocked || ["a", "code", "pre"].includes(node.tagName || "");
      if (!node.children || nextBlocked) return;
      const nextChildren: HastNode[] = [];
      node.children.forEach((child) => {
        if (child.type === "text" && child.value && ROOM_REFERENCE_PATTERN.test(child.value)) {
          ROOM_REFERENCE_PATTERN.lastIndex = 0;
          nextChildren.push(...roomReferenceNodes(child.value, mentionLabels));
        } else {
          ROOM_REFERENCE_PATTERN.lastIndex = 0;
          visit(child, nextBlocked);
          nextChildren.push(child);
        }
      });
      node.children = nextChildren;
    };
    visit(tree);
  };
}

export default function DiscordText({
  text,
  mentionLabels = {},
}: {
  text: string;
  mentionLabels?: MentionLabels;
}) {
  const previews = linkPreviewsForText(text);
  return (
    <>
      <div className="dc-markdown">
        <ReactMarkdown
          remarkPlugins={[[remarkGfm, { singleTilde: false }]]}
          rehypePlugins={[[rehypeRoomReferences, { mentionLabels }]]}
          skipHtml
          components={{
            img: ({ alt, src }) => (
              <a
                className="dc-chat-link"
                href={src}
                target="_blank"
                rel="noreferrer"
                data-markdown-image-link
              >
                {alt || "이미지 링크"}
              </a>
            ),
            a: ({ children, href }) => (
              <a className="dc-chat-link" href={href} target="_blank" rel="noreferrer">
                {children}
              </a>
            ),
            code: ({ children, className }) => {
              const block = Boolean(className);
              return block ? (
                <code className={className}>{children}</code>
              ) : (
                <code className="dc-inline-code">{children}</code>
              );
            },
            table: ({ children }) => (
              <div className="dc-markdown-table-wrap">
                <table>{children}</table>
              </div>
            ),
          }}
        >
          {repairMarkdownTables(text)}
        </ReactMarkdown>
      </div>
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
