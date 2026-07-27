export type ProviderBrandKey =
  | "codex"
  | "antigravity"
  | "grok"
  | "claude"
  | "cursor"
  | "opencode"
  | "deepseek";

export type ProviderBrand = {
  label: string;
  background: string;
};

const PROVIDER_ALIASES: Record<string, ProviderBrandKey> = {
  codex: "codex",
  codex_cli: "codex",
  codex_live_session: "codex",
  antigravity: "antigravity",
  antigravity_cli: "antigravity",
  antigravity_live_session: "antigravity",
  agy: "antigravity",
  grok: "grok",
  grok_live_session: "grok",
  claude: "claude",
  claude_code: "claude",
  cursor: "cursor",
  cursor_live_session: "cursor",
  opencode: "opencode",
  opencode_server: "opencode",
  deepseek: "deepseek",
  deepseek_api: "deepseek",
};

export const PROVIDER_BRANDS: Record<ProviderBrandKey, ProviderBrand> = {
  codex: {
    label: "OpenAI",
    background: "linear-gradient(145deg, #19c37d, #0a8f68)",
  },
  antigravity: {
    label: "Google Gemini",
    background: "linear-gradient(145deg, #3186ff 8%, #8d64ff 56%, #dc6bca)",
  },
  grok: {
    label: "Grok",
    background: "linear-gradient(145deg, #252a50 12%, #684dff)",
  },
  claude: {
    label: "Claude",
    background: "linear-gradient(145deg, #e48b6c, #c65f40)",
  },
  cursor: {
    label: "Cursor",
    background: "linear-gradient(145deg, #2b3045 12%, #5068ed)",
  },
  opencode: {
    label: "OpenCode",
    background: "linear-gradient(145deg, #254331 12%, #32a65a)",
  },
  deepseek: {
    label: "DeepSeek",
    background: "linear-gradient(145deg, #6280ff, #3651d6)",
  },
};

function normalizeProviderIdentifier(value?: string) {
  return String(value || "").trim().toLowerCase().replaceAll("-", "_");
}

export function providerBrandKey(
  providerId?: string,
  providerKind?: string
): ProviderBrandKey | undefined {
  return (
    PROVIDER_ALIASES[normalizeProviderIdentifier(providerId)] ||
    PROVIDER_ALIASES[normalizeProviderIdentifier(providerKind)]
  );
}
