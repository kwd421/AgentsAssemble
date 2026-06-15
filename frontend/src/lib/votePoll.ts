export type VoteCommand = {
  question: string;
  options: string[];
};

export const VOTE_COMMAND_USAGE = "/vote 질문 | 옵션1 | 옵션2 [| 옵션3 ...]";

/** Parse "/vote 질문 | 옵션1 | 옵션2" (or newline-separated) from composer text.
 * Returns null when the text is not a /vote command; throws with a usage
 * message when it is one but malformed. */
export function parseVoteCommand(text: string): VoteCommand | null {
  const trimmed = String(text || "").trim();
  if (!/^\/vote(\s|$)/i.test(trimmed)) return null;
  const body = trimmed.replace(/^\/vote\s*/i, "");
  const parts = body
    .split(/\||\n/)
    .map((part) => part.trim())
    .filter(Boolean);
  const question = parts[0] || "";
  const seen = new Set<string>();
  const options: string[] = [];
  for (const option of parts.slice(1)) {
    const key = option.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    options.push(option.slice(0, 100));
    if (options.length >= 10) break;
  }
  if (!question || options.length < 2) {
    throw new Error(`투표 형식: ${VOTE_COMMAND_USAGE}`);
  }
  return { question: question.slice(0, 300), options };
}
