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
  foreground: string;
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
    background: "#ffffff",
    foreground: "#0a8f68",
  },
  antigravity: {
    label: "Google Gemini",
    background: "#ffffff",
    foreground: "#7257d9",
  },
  grok: {
    label: "Grok",
    background: "#ffffff",
    foreground: "#171717",
  },
  claude: {
    label: "Claude",
    background: "#ffffff",
    foreground: "#c65f40",
  },
  cursor: {
    label: "Cursor",
    background: "#ffffff",
    foreground: "#2b3045",
  },
  opencode: {
    label: "OpenCode",
    background: "#ffffff",
    foreground: "#238149",
  },
  deepseek: {
    label: "DeepSeek",
    background: "#ffffff",
    foreground: "#4366e8",
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
