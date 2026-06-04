export type MentionQuery = {
  start: number;
  query: string;
};

function cleanMentionName(name: string) {
  return name
    .replace(/[\r\n<>]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

export function mentionQueryAtCursor(message: string, cursor = message.length): MentionQuery | null {
  const safeCursor = Math.max(0, Math.min(cursor, message.length));
  const beforeCursor = message.slice(0, safeCursor);
  const match = /(^|\s)@((?:[^\s@#:`*~<>\r\n][^@#:`*~<>\r\n]{0,47})?)$/u.exec(beforeCursor);
  if (!match) return null;
  return {
    start: beforeCursor.length - match[2].length - 1,
    query: match[2].replace(/\s+/g, " ").trim().toLowerCase(),
  };
}

export function mentionOptions(
  mentionables: string[],
  query: MentionQuery | null,
  limit = 6
): string[] {
  if (!query) return [];
  const seen = new Set<string>();
  const options: string[] = [];
  for (const rawName of mentionables) {
    const name = cleanMentionName(rawName);
    if (!name) continue;
    const key = name.toLowerCase();
    if (seen.has(key) || !key.includes(query.query)) continue;
    seen.add(key);
    options.push(name);
    if (options.length >= limit) break;
  }
  return options;
}

export function formatMentionToken(name: string): string {
  const cleanName = cleanMentionName(name);
  if (!cleanName) return "@";
  if (/\s/u.test(cleanName)) return `<@${cleanName}>`;
  return `@${cleanName}`;
}

export function insertMentionText(
  message: string,
  cursor: number,
  query: MentionQuery | null,
  name: string
): { message: string; cursor: number } {
  const safeCursor = Math.max(0, Math.min(cursor, message.length));
  if (!query) {
    return {
      message,
      cursor: safeCursor,
    };
  }
  const token = `${formatMentionToken(name)} `;
  const start = Math.max(0, Math.min(query.start, safeCursor));
  const nextMessage = `${message.slice(0, start)}${token}${message.slice(safeCursor)}`;
  return {
    message: nextMessage,
    cursor: start + token.length,
  };
}
